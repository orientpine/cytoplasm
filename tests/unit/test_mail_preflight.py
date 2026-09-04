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
import mail_quote  # noqa: E402
import triage_cli  # noqa: E402
import triage_core  # noqa: E402
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


# --- the quoted original survives the execution boundary ----------------------------
#
# 2026-09-03: a forward draft whose ``quote`` held 3,530 chars was sent with ``--body``
# equal to the 266-char reply text alone — the recipient never received the mail being
# answered. The guard payload carries only the owner-reviewed reply text, so the
# boundary that rebuilds the frozen argv must re-attach the quote.

QUOTE_TO = "peer@example.invalid"
QUOTE_CC = "cc-one@example.invalid"
QUOTE_SUBJECT = "Re: 견적 확인 요청"
QUOTE_ORIGINAL_BODY = "다음 주 회의 참석 가능 여부 회신 부탁드립니다.\n\n감사합니다."
QUOTED_ORIGINAL = (
    f"{mail_quote.SEPARATOR}\n"
    "보낸 사람: 가상 발신자 <peer@example.invalid>\n"
    "보낸 날짜: 2026-08-30 15:12\n"
    "받는 사람: owner@example.invalid\n"
    f"참조: {QUOTE_CC}\n"
    "제목: 견적 확인 요청\n"
    f"\n{QUOTE_ORIGINAL_BODY}"
)
QUOTE_REPLY = "송아 이사님께 확인 후 회신드리겠습니다."
QUOTE_REPLY_NORMALIZED = "송화 이사님께 확인 후 회신드리겠습니다."


def _quote_draft(*, body: str, quote: str) -> dict[str, JsonValue]:
    """A pending mailon draft exactly as ``triage_gate.create_draft`` persists it."""
    record: dict[str, JsonValue] = {
        "argv": list(
            triage_core.build_send_argv(
                "python3", QUOTE_TO, QUOTE_SUBJECT, mail_quote.with_quote(body, quote), (), QUOTE_CC
            )
        ),
        "body": body,
        "category": "reply",
        "cc": QUOTE_CC,
        "channel_id": "",
        "created": "2026-09-03T00:00:00Z",
        "flags": [],
        "id": "abc123",
        "kind": "reply",
        "mail_subject": "견적 확인 요청",
        "message_id": "",
        "sender": "가상 발신자 <peer@example.invalid>",
        "sender_masked": triage_core.mask_value(QUOTE_TO),
        "sensitive": False,
        "status": "pending",
        "subject": QUOTE_SUBJECT,
        "surface": None,
        "tags": [],
        "to": QUOTE_TO,
        "uid": "u-1",
        "uid_opaque": triage_core.mask_value("u-1"),
        "policy_version": None,
    }
    if quote:  # absent on legacy/no-quote drafts, exactly like the gate persists them
        record["quote"] = quote
    record["sha256"] = triage_core.draft_sha256(record)
    return record


def _quote_payload(body: str) -> dict[str, JsonValue]:
    """What the entity preflight returns: the reply text only, never the quote."""
    return {"to": QUOTE_TO, "cc": QUOTE_CC, "subject": QUOTE_SUBJECT, "body": body}


def _argv_option(argv: JsonValue, option: str) -> str:
    assert isinstance(argv, list)
    value = argv[argv.index(option) + 1]
    assert isinstance(value, str)
    return value


def test_mail_preflight_when_draft_quotes_the_original_then_sends_it_below_the_reply() -> None:
    # Given: an approved reply draft whose frozen argv quotes the answered mail.
    import mail_preflight  # noqa: E402

    draft = _quote_draft(body=QUOTE_REPLY, quote=QUOTED_ORIGINAL)

    # When: the execution boundary rebuilds the argv from the preflight payload.
    updated = mail_preflight._draft_with_payload(draft, _quote_payload(QUOTE_REPLY))

    # Then: what actually goes out is the reply text followed by the original mail.
    sent = _argv_option(updated["argv"], "--body")
    assert sent == mail_quote.with_quote(QUOTE_REPLY, QUOTED_ORIGINAL)
    assert mail_quote.SEPARATOR in sent
    assert QUOTE_ORIGINAL_BODY in sent
    assert updated["body"] == QUOTE_REPLY
    assert updated["quote"] == QUOTED_ORIGINAL
    assert updated["sha256"] == triage_core.draft_sha256(updated)


def test_mail_preflight_when_reply_text_is_normalized_then_quote_rides_below_the_new_body() -> None:
    # Given: the preflight rewrote a personal name inside the reply text only.
    import mail_preflight  # noqa: E402

    draft = _quote_draft(body=QUOTE_REPLY, quote=QUOTED_ORIGINAL)

    # When: the rewritten reply crosses the same boundary.
    updated = mail_preflight._draft_with_payload(draft, _quote_payload(QUOTE_REPLY_NORMALIZED))

    # Then: the quote follows the NEW body and the original text is never normalized.
    sent = _argv_option(updated["argv"], "--body")
    assert sent == mail_quote.with_quote(QUOTE_REPLY_NORMALIZED, QUOTED_ORIGINAL)
    assert sent.startswith(QUOTE_REPLY_NORMALIZED)
    assert updated["body"] == QUOTE_REPLY_NORMALIZED
    assert updated["quote"] == QUOTED_ORIGINAL
    assert updated["sha256"] == triage_core.draft_sha256(updated)


def test_mail_preflight_when_draft_has_no_quote_then_argv_and_hash_stay_byte_identical() -> None:
    # Given: a legacy draft persisted without a quote field.
    import mail_preflight  # noqa: E402

    draft = _quote_draft(body=QUOTE_REPLY, quote="")
    assert "quote" not in draft

    # When: it crosses the boundary with an unchanged payload.
    updated = mail_preflight._draft_with_payload(draft, _quote_payload(QUOTE_REPLY))

    # Then: argv and hash match the pre-fix plain-body behavior byte for byte.
    assert updated["argv"] == list(
        triage_core.build_send_argv("python3", QUOTE_TO, QUOTE_SUBJECT, QUOTE_REPLY, (), QUOTE_CC)
    )
    assert "quote" not in updated
    assert updated["sha256"] == draft["sha256"]
    assert updated["sha256"] == triage_core.draft_sha256(updated)
