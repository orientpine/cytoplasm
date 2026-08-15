from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import gmail_approval_gate as gmail_gate  # noqa: E402
import mail_gmail_send  # noqa: E402
import triage_cli  # noqa: E402
import triage_gate  # noqa: E402
from automation.entity_preflight.contracts import (  # noqa: E402
    CandidateResolver,
    DetectedEntity,
    EntityKind,
    JsonValue,
    PreflightInput,
    RelationshipQuery,
    ResolutionCandidate,
    SourceKind,
)
from automation.entity_preflight.gate import (  # noqa: E402
    GateDependencies,
    GuardRequest,
    InMemoryIdempotencyStore,
)


class _FixtureSource(CandidateResolver):
    source = SourceKind.PERSONAL_RAG

    def __init__(self, candidates: tuple[ResolutionCandidate, ...], *, fails: bool = False) -> None:
        self.candidates = candidates
        self.fails = fails
        self.calls = 0

    def resolve(
        self,
        request: PreflightInput,
        query: RelationshipQuery | None,
    ) -> tuple[ResolutionCandidate, ...]:
        del request, query
        self.calls += 1
        if self.fails:
            raise TimeoutError("offline resolver timeout")
        return self.candidates


class _AuditSink:
    def append(self, event: Mapping[str, JsonValue]) -> str:
        del event
        return "private://test/mail-preflight"


def _dependencies() -> GateDependencies:
    return GateDependencies(_AuditSink(), None, InMemoryIdempotencyStore())


def _gmail_draft() -> dict[str, JsonValue]:
    action = mail_gmail_send.build_action(
        mail_gmail_send.NewMailRequest(
            options=mail_gmail_send.DeliveryOptions(account="gmail"),
            to="송아 <recipient@example.test>",
            subject="송아 검토 요청",
            body="송아에게 최종본을 보내세요.",
        )
    )
    snapshot = gmail_gate.build_approval_snapshot(action)
    return gmail_gate.approval_draft(snapshot, draft_id="mail-preflight", created_at="runtime")


def _request(
    draft: Mapping[str, JsonValue],
    source: _FixtureSource,
    *,
    include_entity: bool = True,
) -> GuardRequest:
    to = _text(draft, "to")
    subject = _text(draft, "subject")
    body = _text(draft, "body")
    raw_text = "\n".join((to, subject, body))
    entities = tuple(
        DetectedEntity(f"m-{index}", "송아", EntityKind.PERSON, start, start + 2)
        for index, start in enumerate(_positions(raw_text, "송아"), 1)
    ) if include_entity else ()
    return GuardRequest(
        request=PreflightInput(
            request_id="mail-preflight-request",
            raw_text=raw_text,
            target_system="mail",
            operation="send",
            entities=entities,
        ),
        payload={"to": to, "subject": subject, "body": body},
        sources=(source,),
        idempotency_key="mail-preflight-request",
        actor="owner",
        purpose="mail_send",
        requested_at="runtime",
    )


def _candidate(mention_id: str, value: str, confidence: float) -> ResolutionCandidate:
    return ResolutionCandidate(
        candidate_id=f"candidate-{mention_id}-{value}",
        mention_id=mention_id,
        source=SourceKind.PERSONAL_RAG,
        normalized_value=value,
        display_value=value,
        confidence=confidence,
        source_ref=f"private://test/{mention_id}/{value}",
    )


def _positions(text: str, value: str) -> tuple[int, ...]:
    positions: list[int] = []
    position = text.find(value)
    while position >= 0:
        positions.append(position)
        position = text.find(value, position + len(value))
    return tuple(positions)


def _text(draft: Mapping[str, JsonValue], field: str) -> str:
    value = draft.get(field)
    assert isinstance(value, str)
    return value


def test_mail_preflight_when_high_confidence_candidate_then_normalizes_final_gmail_snapshot_before_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a Gmail approval draft with the same transcribed personal name in all send fields.
    import mail_preflight  # noqa: E402

    draft = _gmail_draft()
    mentions = ("m-1", "m-2", "m-3")
    source = _FixtureSource(tuple(_candidate(mention_id, "송화", 0.98) for mention_id in mentions))
    request = _request(draft, source)
    executed: list[Mapping[str, JsonValue]] = []
    monkeypatch.setattr(mail_preflight, "mail_guard_request", lambda _draft: request)
    monkeypatch.setattr(triage_gate, "execute_draft", lambda current, _approval: executed.append(current))

    # When: the one mail execution adapter crosses the shared guard.
    mail_preflight.guarded_execute_draft(draft, triage_gate.Approval("ref", "test", "owner"), _dependencies())

    # Then: exactly one normalized action reaches the write boundary and its snapshot is final.
    assert len(executed) == 1
    normalized = executed[0]
    snapshot = gmail_gate.snapshot_from_draft(normalized)
    approval_record = gmail_gate.approval_record(
        snapshot,
        gmail_gate.OwnerApproval(
            owner_id="owner",
            message_id="fixture",
            approved_at=datetime(2026, 7, 29, tzinfo=UTC),
            expires_at=datetime(2026, 7, 29, tzinfo=UTC) + timedelta(minutes=15),
        ),
    )
    assert snapshot.recipients == "송화 <recipient@example.test>"
    assert snapshot.subject == "송화 검토 요청"
    assert snapshot.body == "송화에게 최종본을 보내세요."
    assert approval_record["approval_snapshot"]["recipients"] == "송화 <recipient@example.test>"
    assert "송아" not in "\n".join(snapshot.argv)


def test_mail_preflight_when_candidates_conflict_then_clarifies_once_without_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two credible normalized values for every mention in an outbound Gmail draft.
    import mail_preflight  # noqa: E402

    draft = _gmail_draft()
    candidates = tuple(
        candidate
        for mention_id in ("m-1", "m-2", "m-3")
        for candidate in (_candidate(mention_id, "송화", 0.98), _candidate(mention_id, "송희", 0.97))
    )
    request = _request(draft, _FixtureSource(candidates))
    executed: list[Mapping[str, JsonValue]] = []
    dependencies = _dependencies()
    monkeypatch.setattr(mail_preflight, "mail_guard_request", lambda _draft: request)
    monkeypatch.setattr(triage_gate, "execute_draft", lambda current, _approval: executed.append(current))

    # When: the conflict reaches the pre-write boundary and then retries with the same key.
    with pytest.raises(mail_preflight.MailPreflightError) as first:
        mail_preflight.guarded_execute_draft(draft, triage_gate.Approval("ref", "test", "owner"), dependencies)
    with pytest.raises(mail_preflight.MailPreflightError) as retry:
        mail_preflight.guarded_execute_draft(draft, triage_gate.Approval("ref", "test", "owner"), dependencies)

    # Then: one owner-facing clarify is rendered, the retry is silent, and send never starts.
    assert first.value.should_render is True
    assert str(first.value).startswith("ENTITY-CLARIFY")
    assert "송화" in str(first.value) and "송희" in str(first.value)
    assert retry.value.should_render is False
    assert executed == []


def test_mail_preflight_when_resolver_times_out_then_fails_closed_without_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a detected personal entity whose resolver times out.
    import mail_preflight  # noqa: E402

    draft = _gmail_draft()
    request = _request(draft, _FixtureSource((), fails=True))
    executed: list[Mapping[str, JsonValue]] = []
    monkeypatch.setattr(mail_preflight, "mail_guard_request", lambda _draft: request)
    monkeypatch.setattr(triage_gate, "execute_draft", lambda current, _approval: executed.append(current))

    # When / Then: unavailable resolution refuses before the mail gate can send.
    with pytest.raises(mail_preflight.MailPreflightError, match="ENTITY-PREFLIGHT-FAIL"):
        mail_preflight.guarded_execute_draft(draft, triage_gate.Approval("ref", "test", "owner"), _dependencies())
    assert executed == []


def test_mail_preflight_when_no_personal_entity_then_preserves_one_send_without_resolver_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an ordinary outgoing mail and a source that would fail if it were queried.
    import mail_preflight  # noqa: E402

    draft = _gmail_draft()
    source = _FixtureSource((), fails=True)
    request = _request(draft, source, include_entity=False)
    executed: list[Mapping[str, JsonValue]] = []
    dependencies = _dependencies()
    monkeypatch.setattr(mail_preflight, "mail_guard_request", lambda _draft: request)
    monkeypatch.setattr(triage_gate, "execute_draft", lambda current, _approval: executed.append(current))

    # When: the same send request is executed and retried.
    mail_preflight.guarded_execute_draft(draft, triage_gate.Approval("ref", "test", "owner"), dependencies)
    mail_preflight.guarded_execute_draft(draft, triage_gate.Approval("ref", "test", "owner"), dependencies)

    # Then: no resolver latency is added and idempotency keeps the connector at one call.
    assert source.calls == 0
    assert executed == [draft]


def test_mail_cli_when_candidates_conflict_then_prints_one_clarify_and_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an approved draft that becomes ambiguous before the existing send boundary.
    import mail_preflight  # noqa: E402

    draft = _gmail_draft()
    candidates = tuple(
        candidate
        for mention_id in ("m-1", "m-2", "m-3")
        for candidate in (_candidate(mention_id, "송화", 0.98), _candidate(mention_id, "송희", 0.97))
    )
    request = _request(draft, _FixtureSource(candidates))
    monkeypatch.setattr(mail_preflight, "mail_guard_request", lambda _draft: request)
    monkeypatch.setattr(triage_gate, "load_draft", lambda _draft_id: draft)
    monkeypatch.setattr(triage_cli.triage_confirm, "confirm_via_injection", lambda _draft, _path: "fixture")
    monkeypatch.setattr(triage_cli.triage_confirm, "owner_id", lambda: "owner")
    monkeypatch.setattr(
        triage_gate,
        "execute_draft",
        lambda _draft, _approval: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    # When: the real confirm command reaches the mail preflight adapter.
    with pytest.raises(triage_gate.GateError) as caught:
        triage_cli.cmd_confirm(argparse.Namespace(draft="mail-preflight", injection_file="fixture.json"))

    # Then: stdout contains one deterministic clarify marker and no external write starts.
    captured = capsys.readouterr()
    assert caught.value.exit_code == 6
    assert captured.out.count("ENTITY-CLARIFY") == 1
    assert "송화" in captured.out and "송희" in captured.out
