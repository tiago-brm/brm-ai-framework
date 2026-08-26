import hashlib
from typing import Any

import pytest
from pydantic import ValidationError

from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.interceptor import intercept
from mcp_engine.core.models import (
    AuditDecision,
    AuditEvent,
    ClientConfig,
    DLPPattern,
    HttpMethod,
    InterceptDecision,
    SkillCall,
)

CPF_DENY = DLPPattern(pattern=r"\d{3}\.\d{3}\.\d{3}-\d{2}", label="CPF", action="deny")
ACCOUNT_MASK = DLPPattern(pattern=r"ACCT-\d{6}", label="Account", action="mask")


class RecordingAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def iter_events(self, client_id: str):
        return (e for e in self.events if e.client_id == client_id)


def _config(**overrides: Any) -> ClientConfig:
    base: dict[str, Any] = {
        "client_id": "acme",
        "domain_allowlist": ("api.example.com", "*.partner.co"),
    }
    return ClientConfig(**{**base, **overrides})


def _call(url: str, response_text: str = "") -> SkillCall:
    return SkillCall(
        skill_name="consulta-cep",
        url=url,
        method=HttpMethod.GET,
        actor="mcp_server",
        response_text=response_text,
    )


def test_recording_audit_sink_satisfies_protocol():
    assert isinstance(RecordingAuditSink(), AuditSink)


def test_domain_in_allowlist_is_allowed():
    sink = RecordingAuditSink()

    decision = intercept(_call("https://api.example.com/cep/01310100"), _config(), sink)

    assert decision.allowed is True
    assert decision.reason is None


def test_domain_not_in_allowlist_is_blocked():
    sink = RecordingAuditSink()

    decision = intercept(_call("https://api.evil.com/exfil"), _config(), sink)

    assert decision.allowed is False
    assert decision.reason == "domain not in allowlist: api.evil.com"


def test_wildcard_allowlist_matches_subdomain():
    sink = RecordingAuditSink()

    decision = intercept(_call("https://billing.partner.co/invoices"), _config(), sink)

    assert decision.allowed is True


def test_wildcard_does_not_match_bare_domain():
    sink = RecordingAuditSink()

    decision = intercept(_call("https://partner.co/invoices"), _config(), sink)

    assert decision.allowed is False


def test_allowlist_match_is_case_insensitive():
    sink = RecordingAuditSink()
    config = _config(domain_allowlist=("API.Example.COM",))

    decision = intercept(_call("https://api.example.COM/cep"), config, sink)

    assert decision.allowed is True


def test_lookalike_domain_suffix_is_blocked():
    sink = RecordingAuditSink()

    decision = intercept(_call("https://api.example.com.evil.io/cep"), _config(), sink)

    assert decision.allowed is False


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "10.0.0.5", "172.16.3.9", "192.168.1.1", "[::1]"],
)
def test_private_ip_is_blocked_even_when_allowlisted(host: str):
    sink = RecordingAuditSink()
    bare_host = host.strip("[]")
    config = _config(domain_allowlist=(bare_host,))

    decision = intercept(_call(f"http://{host}:8080/admin"), config, sink)

    assert decision.allowed is False
    assert decision.reason == f"private IP blocked: {bare_host}"


def test_private_ip_allowed_when_block_private_ip_disabled():
    sink = RecordingAuditSink()
    config = _config(domain_allowlist=("10.0.0.5",), block_private_ip=False)

    decision = intercept(_call("http://10.0.0.5/legacy"), config, sink)

    assert decision.allowed is True


def test_private_ip_outside_allowlist_is_blocked_by_allowlist_first():
    sink = RecordingAuditSink()

    decision = intercept(_call("http://127.0.0.1/admin"), _config(), sink)

    assert decision.allowed is False
    assert decision.reason == "domain not in allowlist: 127.0.0.1"


def test_public_ip_is_not_treated_as_private():
    sink = RecordingAuditSink()
    config = _config(domain_allowlist=("8.8.8.8",))

    decision = intercept(_call("http://8.8.8.8/resolve"), config, sink)

    assert decision.allowed is True


def test_dlp_deny_pattern_blocks_response():
    sink = RecordingAuditSink()
    config = _config(dlp_patterns=(CPF_DENY,))
    call = _call("https://api.example.com/cliente", response_text="CPF 123.456.789-00 ok")

    decision = intercept(call, config, sink)

    assert decision.allowed is False
    assert decision.reason == "DLP: found CPF"
    assert decision.dlp_matches == {"CPF": ["123.456.789-00"]}


def test_dlp_mask_pattern_reports_without_blocking():
    sink = RecordingAuditSink()
    config = _config(dlp_patterns=(ACCOUNT_MASK,))
    call = _call("https://api.example.com/conta", response_text="saldo ACCT-123456 ok")

    decision = intercept(call, config, sink)

    assert decision.allowed is True
    assert decision.reason is None
    assert decision.dlp_matches == {"Account": ["ACCT-123456"]}


def test_dlp_deny_wins_over_mask_and_reports_all_matches():
    sink = RecordingAuditSink()
    config = _config(dlp_patterns=(ACCOUNT_MASK, CPF_DENY))
    call = _call(
        "https://api.example.com/cliente",
        response_text="ACCT-123456 / 123.456.789-00",
    )

    decision = intercept(call, config, sink)

    assert decision.allowed is False
    assert decision.reason == "DLP: found CPF"
    assert decision.dlp_matches == {"Account": ["ACCT-123456"], "CPF": ["123.456.789-00"]}


def test_dlp_collects_multiple_matches_for_one_pattern():
    sink = RecordingAuditSink()
    config = _config(dlp_patterns=(ACCOUNT_MASK,))
    call = _call("https://api.example.com/contas", response_text="ACCT-111111 ACCT-222222")

    decision = intercept(call, config, sink)

    assert decision.dlp_matches == {"Account": ["ACCT-111111", "ACCT-222222"]}


def test_dlp_not_applied_when_no_pattern_matches():
    sink = RecordingAuditSink()
    config = _config(dlp_patterns=(CPF_DENY,))
    call = _call("https://api.example.com/cliente", response_text="nada sensivel aqui")

    decision = intercept(call, config, sink)

    assert decision.allowed is True
    assert decision.dlp_matches == {}


def test_dlp_is_skipped_for_blocked_domain():
    sink = RecordingAuditSink()
    config = _config(dlp_patterns=(CPF_DENY,))
    call = _call("https://api.evil.com/exfil", response_text="123.456.789-00")

    decision = intercept(call, config, sink)

    assert decision.reason == "domain not in allowlist: api.evil.com"
    assert decision.dlp_matches == {}


def test_audit_event_recorded_for_allowed_call():
    sink = RecordingAuditSink()

    intercept(_call("https://api.example.com/cep"), _config(), sink)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.client_id == "acme"
    assert event.actor == "mcp_server"
    assert event.action == "consulta-cep"
    assert event.decision is AuditDecision.ALLOWED
    assert event.details["domain"] == "api.example.com"
    assert event.details["dlp_matches"] == {}
    assert event.occurred_at.tzinfo is not None


def test_audit_event_recorded_for_blocked_domain():
    sink = RecordingAuditSink()

    intercept(_call("https://api.evil.com/exfil"), _config(), sink)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.decision is AuditDecision.DENIED
    assert event.details["domain"] == "api.evil.com"


def test_audit_event_carries_dlp_matches():
    sink = RecordingAuditSink()
    config = _config(dlp_patterns=(CPF_DENY,))
    call = _call("https://api.example.com/cliente", response_text="123.456.789-00")

    intercept(call, config, sink)

    event = sink.events[0]
    assert event.decision is AuditDecision.DENIED
    assert event.details["dlp_matches"] == {"CPF": ["123.456.789-00"]}


def test_payload_digest_is_sha256_of_response():
    sink = RecordingAuditSink()
    config = _config(dlp_patterns=(CPF_DENY,))
    response = "CPF 123.456.789-00"
    call = _call("https://api.example.com/cliente", response_text=response)

    intercept(call, config, sink)

    expected = hashlib.sha256(response.encode("utf-8")).hexdigest()
    assert sink.events[0].payload_digest == expected


def test_payload_digest_is_none_when_allowed_without_dlp():
    sink = RecordingAuditSink()
    call = _call("https://api.example.com/cep", response_text="01310-100")

    intercept(call, _config(), sink)

    assert sink.events[0].payload_digest is None


def test_intercept_decision_is_immutable():
    decision = InterceptDecision(allowed=True)

    with pytest.raises(ValidationError):
        decision.allowed = False


def test_client_config_requires_at_least_one_allowed_domain():
    with pytest.raises(ValidationError):
        ClientConfig(client_id="acme", domain_allowlist=())


def test_dlp_pattern_rejects_unknown_action():
    with pytest.raises(ValidationError):
        DLPPattern(pattern=r"\d+", label="Num", action="redact")
