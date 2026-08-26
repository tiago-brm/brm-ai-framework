import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from audit.sink import AuditChainError, FileSystemAuditSink
from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.models import AuditDecision, AuditEvent


class TestFileSystemAuditSink:
    def test_record_valid_event(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)
        event = AuditEvent(
            client_id="acme",
            occurred_at=datetime.now(UTC),
            actor="claude",
            action="consultar_valor",
            decision=AuditDecision.ALLOWED,
        )

        sink.record(event)

        assert isinstance(sink, AuditSink)
        audit_file = tmp_path / "acme" / "audit.jsonl"
        assert audit_file.exists()
        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["client_id"] == "acme"
        assert payload["actor"] == "claude"
        assert payload["previous_hash"] is None

    def test_append_second_event_creates_hash_chain(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)
        event1 = AuditEvent(
            client_id="acme",
            occurred_at=datetime.now(UTC),
            actor="claude",
            action="action1",
            decision=AuditDecision.ALLOWED,
        )
        event2 = AuditEvent(
            client_id="acme",
            occurred_at=datetime.now(UTC),
            actor="claude",
            action="action2",
            decision=AuditDecision.DENIED,
        )

        sink.record(event1)
        sink.record(event2)

        audit_file = tmp_path / "acme" / "audit.jsonl"
        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 2

        payload1 = json.loads(lines[0])
        payload2 = json.loads(lines[1])

        assert payload1["previous_hash"] is None
        assert payload2["previous_hash"] is not None
        assert isinstance(payload2["previous_hash"], str)
        assert len(payload2["previous_hash"]) == 64

    def test_iter_events_reproduces_chain(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)
        events = [
            AuditEvent(
                client_id="acme",
                occurred_at=datetime.now(UTC),
                actor="claude",
                action=f"action{i}",
                decision=AuditDecision.ALLOWED if i % 2 == 0 else AuditDecision.DENIED,
            )
            for i in range(3)
        ]

        for event in events:
            sink.record(event)

        recovered = list(sink.iter_events("acme"))

        assert len(recovered) == 3
        for i, event in enumerate(recovered):
            assert event.actor == "claude"
            assert event.action == f"action{i}"
            assert event.client_id == "acme"

    def test_iter_events_empty_file(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)

        events = list(sink.iter_events("nonexistent"))

        assert events == []

    def test_iter_events_with_empty_lines_skipped(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)
        event = AuditEvent(
            client_id="acme",
            occurred_at=datetime.now(UTC),
            actor="claude",
            action="test",
            decision=AuditDecision.ALLOWED,
        )
        sink.record(event)

        audit_file = tmp_path / "acme" / "audit.jsonl"
        audit_file.write_text(audit_file.read_text() + "\n\n")

        recovered = list(sink.iter_events("acme"))

        assert len(recovered) == 1

    def test_chain_broken_by_edited_line_detected(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)
        event1 = AuditEvent(
            client_id="acme",
            occurred_at=datetime.now(UTC),
            actor="actor1",
            action="action1",
            decision=AuditDecision.ALLOWED,
        )
        event2 = AuditEvent(
            client_id="acme",
            occurred_at=datetime.now(UTC),
            actor="actor2",
            action="action2",
            decision=AuditDecision.DENIED,
        )
        sink.record(event1)
        sink.record(event2)

        audit_file = tmp_path / "acme" / "audit.jsonl"
        lines = audit_file.read_text().strip().split("\n")

        edited_line1 = json.loads(lines[0])
        edited_line1["actor"] = "attacker"
        lines[0] = json.dumps(edited_line1, sort_keys=True)

        audit_file.write_text("\n".join(lines) + "\n")

        with pytest.raises(AuditChainError) as exc_info:
            list(sink.iter_events("acme"))

        assert exc_info.value.client_id == "acme"
        assert exc_info.value.line_number == 2

    def test_chain_broken_by_deleted_line_detected(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)
        events = [
            AuditEvent(
                client_id="acme",
                occurred_at=datetime.now(UTC),
                actor="claude",
                action=f"action{i}",
                decision=AuditDecision.ALLOWED,
            )
            for i in range(3)
        ]

        for event in events:
            sink.record(event)

        audit_file = tmp_path / "acme" / "audit.jsonl"
        lines = audit_file.read_text().strip().split("\n")

        lines.pop(1)

        audit_file.write_text("\n".join(lines) + "\n")

        with pytest.raises(AuditChainError) as exc_info:
            list(sink.iter_events("acme"))

        assert exc_info.value.client_id == "acme"
        assert exc_info.value.line_number == 2

    def test_creates_directory_if_not_exists(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)
        event = AuditEvent(
            client_id="new-client",
            occurred_at=datetime.now(UTC),
            actor="claude",
            action="first_action",
            decision=AuditDecision.ALLOWED,
        )

        sink.record(event)

        audit_file = tmp_path / "new-client" / "audit.jsonl"
        assert audit_file.exists()

    def test_multiple_clients_isolated(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)
        event_acme = AuditEvent(
            client_id="acme",
            occurred_at=datetime.now(UTC),
            actor="claude",
            action="acme_action",
            decision=AuditDecision.ALLOWED,
        )
        event_beta = AuditEvent(
            client_id="beta",
            occurred_at=datetime.now(UTC),
            actor="claude",
            action="beta_action",
            decision=AuditDecision.DENIED,
        )

        sink.record(event_acme)
        sink.record(event_beta)

        acme_events = list(sink.iter_events("acme"))
        beta_events = list(sink.iter_events("beta"))

        assert len(acme_events) == 1
        assert len(beta_events) == 1
        assert acme_events[0].action == "acme_action"
        assert beta_events[0].action == "beta_action"

        acme_file = tmp_path / "acme" / "audit.jsonl"
        beta_file = tmp_path / "beta" / "audit.jsonl"
        assert acme_file.exists()
        assert beta_file.exists()
        assert acme_file != beta_file
