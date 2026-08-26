from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import UTC, datetime
from fnmatch import fnmatch
from functools import lru_cache
from urllib.parse import urlparse

from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.models import (
    AuditDecision,
    AuditEvent,
    ClientConfig,
    InterceptDecision,
    SkillCall,
)


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _is_private_ip(hostname: str) -> bool:
    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False


def _payload_digest(response_text: str, allowed: bool, dlp_configured: bool) -> str | None:
    if allowed and not dlp_configured:
        return None
    return hashlib.sha256(response_text.encode("utf-8")).hexdigest()


def _emit_audit(
    audit_sink: AuditSink,
    skill_call: SkillCall,
    client_config: ClientConfig,
    decision: InterceptDecision,
    hostname: str,
) -> None:
    event = AuditEvent(
        client_id=client_config.client_id,
        occurred_at=datetime.now(UTC),
        actor=skill_call.actor,
        action=skill_call.skill_name,
        decision=AuditDecision.ALLOWED if decision.allowed else AuditDecision.DENIED,
        rule_id=None,
        payload_digest=_payload_digest(
            skill_call.response_text, decision.allowed, bool(client_config.dlp_patterns)
        ),
        details={"domain": hostname, "dlp_matches": decision.dlp_matches},
    )
    audit_sink.record(event)


def intercept(
    skill_call: SkillCall,
    client_config: ClientConfig,
    audit_sink: AuditSink,
) -> InterceptDecision:
    """
    Intercepta execução de skill e aplica 3 controles:
    1. Domain allowlist + IP privado
    2. DLP na resposta
    3. Registra em audit log (append-only, hash-chained)
    """
    hostname = (urlparse(skill_call.url).hostname or "").lower()

    if not any(fnmatch(hostname, pattern.lower()) for pattern in client_config.domain_allowlist):
        decision = InterceptDecision(allowed=False, reason=f"domain not in allowlist: {hostname}")
        _emit_audit(audit_sink, skill_call, client_config, decision, hostname)
        return decision

    if client_config.block_private_ip and _is_private_ip(hostname):
        decision = InterceptDecision(allowed=False, reason=f"private IP blocked: {hostname}")
        _emit_audit(audit_sink, skill_call, client_config, decision, hostname)
        return decision

    dlp_matches: dict[str, list[str]] = {}
    deny_label: str | None = None
    for dlp in client_config.dlp_patterns:
        found = _compile(dlp.pattern).findall(skill_call.response_text)
        if not found:
            continue
        dlp_matches[dlp.label] = list(found)
        if dlp.action == "deny" and deny_label is None:
            deny_label = dlp.label

    if deny_label is not None:
        decision = InterceptDecision(
            allowed=False, reason=f"DLP: found {deny_label}", dlp_matches=dlp_matches
        )
        _emit_audit(audit_sink, skill_call, client_config, decision, hostname)
        return decision

    decision = InterceptDecision(allowed=True, dlp_matches=dlp_matches)
    _emit_audit(audit_sink, skill_call, client_config, decision, hostname)
    return decision
