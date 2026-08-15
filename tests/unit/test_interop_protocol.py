from __future__ import annotations

from datetime import datetime

from automation.interop.chunker import chunk_message
from automation.interop.injection_adapter import (
    InboundEvent,
    accept_test_event,
    sign_event,
    verify_signed_event,
)
from automation.interop.killswitch import PauseStore
from automation.interop.loop_guard import LoopGuard
from automation.interop.report import ReportStatus, TaskReport, format_report, mask_summary, parse_report
from automation.interop.delegation import InteropEnvelope, format_envelope, parse_envelope, response_for


def test_chunk_message_when_five_thousand_characters_then_returns_three_ordered_chunks() -> None:
    # Given
    original = "x" * 5_000

    # When
    chunks = chunk_message(original)

    # Then
    assert len(chunks) == 3
    assert chunks == ["x" * 2_000, "x" * 2_000, "x" * 1_000]
    assert "".join(chunks) == original
    assert all(len(chunk) <= 2_000 for chunk in chunks)


def test_loop_guard_when_sixth_distinct_reply_in_window_then_suppresses() -> None:
    # Given
    guard = LoopGuard()
    now = 1_000.0

    # When
    for index in range(5):
        decision = guard.evaluate(thread_id="thread-a", body=f"body-{index}", now=now)
        assert not decision.suppressed
    sixth = guard.evaluate(thread_id="thread-a", body="body-6", now=now)

    # Then
    assert sixth.suppressed
    assert sixth.reason == "rate_limit"


def test_loop_guard_when_consecutive_body_matches_then_suppresses_immediately() -> None:
    # Given
    guard = LoopGuard()

    # When
    first = guard.evaluate(thread_id="thread-a", body="same", now=1_000.0)
    duplicate = guard.evaluate(thread_id="thread-a", body="same", now=1_001.0)

    # Then
    assert not first.suppressed
    assert duplicate.suppressed
    assert duplicate.reason == "duplicate_body"


def test_loop_guard_when_sustained_low_value_bot_chatter_then_attenuates() -> None:
    # Given
    guard = LoopGuard()

    # When
    first = guard.evaluate(thread_id="thread-a", body="🟢 1", now=1_000.0)
    second = guard.evaluate(thread_id="thread-a", body="🟢 2", now=1_001.0)
    third = guard.evaluate(thread_id="thread-a", body="🟢 3", now=1_002.0)

    # Then
    assert not first.suppressed
    assert not second.suppressed
    assert third.suppressed
    assert third.reason == "low_value_chatter"


def test_loop_guard_when_near_duplicate_bot_chatter_then_suppresses() -> None:
    # Given
    guard = LoopGuard()

    # When
    first = guard.evaluate(thread_id="thread-a", body="integration report checkpoint alpha", now=1_000.0)
    near_duplicate = guard.evaluate(thread_id="thread-a", body="integration report checkpoint beta", now=1_001.0)

    # Then
    assert not first.suppressed
    assert near_duplicate.suppressed
    assert near_duplicate.reason == "near_duplicate_body"


def test_loop_guard_when_valid_interop_envelope_repeats_then_keeps_protocol_flowing() -> None:
    # Given
    guard = LoopGuard()
    envelope = format_envelope(
        InteropEnvelope(
            correlation_id="corr-protocol",
            sender_id="peer-test",
            recipient_id="agent-cha",
            intent="query_availability",
            payload={"duration_min": 30},
        )
    )

    # When
    first = guard.evaluate(thread_id="thread-a", body=envelope, now=1_000.0)
    repeated = guard.evaluate(thread_id="thread-a", body=envelope, now=1_001.0)

    # Then
    assert not first.suppressed
    assert not repeated.suppressed


def test_report_when_formatted_then_parser_round_trips_specification_fields() -> None:
    # Given
    report = TaskReport(
        agent_id="agent-cha",
        task_id="task-123",
        status=ReportStatus.DONE,
        summary="완료",
        links=("https://example.invalid/task",),
        timestamp=datetime.fromisoformat("2026-07-15T14:30:00+09:00"),
    )

    # When
    parsed = parse_report(format_report(report))

    # Then
    assert parsed == report


def test_mask_summary_when_source_filenames_present_then_replaces_with_placeholder() -> None:
    # Given
    raw = "review-required: fix for remove_completed bug — 2 files changed (calendar_cli.py, confirm_reaction_watch.py)"

    # When
    masked = mask_summary(raw)

    # Then
    assert "calendar_cli.py" not in masked
    assert "confirm_reaction_watch.py" not in masked
    assert "[MASKED_PATH]" in masked


def test_mask_summary_when_directory_path_present_then_masks_path() -> None:
    # Given
    raw = "patched automation/interop/report.py handler"

    # When
    masked = mask_summary(raw)

    # Then
    assert "automation/interop/report.py" not in masked
    assert "[MASKED_PATH]" in masked


def test_mask_summary_when_secret_and_id_shapes_present_then_masks_them() -> None:
    # Given
    raw = "deployed with token sk-abcd1234efgh and channel 1526482932175470694"

    # When
    masked = mask_summary(raw)

    # Then
    assert "sk-abcd1234efgh" not in masked
    assert "1526482932175470694" not in masked


def test_mask_summary_when_email_present_then_masks_pii() -> None:
    # Given
    raw = "notified researcher@example.invalid about the block"

    # When
    masked = mask_summary(raw)

    # Then
    assert "researcher@example.invalid" not in masked
    assert "[MASKED_EMAIL]" in masked


def test_mask_summary_when_activity_level_text_then_passes_through_unchanged() -> None:
    # Given
    raw = "calendar 스킬 버그 수정 — 소스 2건·테스트 1건 변경, 전체 통과, 사람 리뷰 대기"

    # When
    masked = mask_summary(raw)

    # Then
    assert masked == raw


def test_format_report_when_summary_has_source_identifier_then_emitted_block_is_masked() -> None:
    # Given
    report = TaskReport(
        agent_id="agent-cha",
        task_id="t_f01929d8",
        status=ReportStatus.BLOCKED,
        summary="review-required: bug in calendar_cli.py",
        links=(),
        timestamp=datetime.fromisoformat("2026-07-21T18:07:04+09:00"),
    )

    # When
    emitted = format_report(report)
    parsed = parse_report(emitted)

    # Then
    assert parsed is not None
    assert "calendar_cli.py" not in emitted
    assert "calendar_cli.py" not in parsed.summary
    assert "[MASKED_PATH]" in parsed.summary

def test_report_when_non_conformant_text_then_parser_returns_none() -> None:
    # Given
    non_conformant = "작업이 끝났습니다"

    # When
    parsed = parse_report(non_conformant)

    # Then
    assert parsed is None


def test_report_when_status_is_not_enum_value_then_parser_returns_none() -> None:
    # Given
    malformed = """```json
{"version":"v0","agent_id":"agent-cha","task_id":"task-123","status":"running","summary":"x","links":[],"timestamp":"2026-07-15T14:30:00+09:00"}
```"""

    # When
    parsed = parse_report(malformed)

    # Then
    assert parsed is None


def test_injection_adapter_when_signature_is_valid_then_accepts_event() -> None:
    # Given
    event = InboundEvent(event_id="event-1", user_id="owner", channel_id="dm", text="ping")
    secret = b"test-secret"
    signature = sign_event(event, secret)

    # When
    verified = verify_signed_event(event, signature, secret)

    # Then
    assert verified


def test_injection_adapter_when_event_is_tampered_then_rejects_signature() -> None:
    # Given
    original = InboundEvent(event_id="event-1", user_id="owner", channel_id="dm", text="ping")
    tampered = InboundEvent(event_id="event-1", user_id="owner", channel_id="dm", text="pause")
    secret = b"test-secret"

    # When
    verified = verify_signed_event(tampered, sign_event(original, secret), secret)

    # Then
    assert not verified


def test_injection_adapter_when_e2e_mode_is_disabled_then_rejects_valid_signature() -> None:
    # Given
    event = InboundEvent(event_id="event-1", user_id="owner", channel_id="dm", text="ping")
    secret = b"test-secret"

    # When
    accepted = accept_test_event(event, sign_event(event, secret), secret, e2e_test_mode=False)

    # Then
    assert not accepted


def test_pause_store_when_owner_pauses_and_resumes_then_persists_then_clears(tmp_path) -> None:
    # Given
    state_file = tmp_path / "paused"
    store = PauseStore(state_file=state_file, owner_id="owner")

    # When
    paused = store.handle(command="!pause-agents", actor_id="owner")
    created = state_file.exists()
    resumed = store.handle(command="!resume-agents", actor_id="owner")

    # Then
    assert paused.accepted
    assert paused.paused
    assert created
    assert resumed.accepted
    assert not resumed.paused
    assert not state_file.exists()


def test_pause_store_when_non_owner_pauses_then_refuses_without_state_change(tmp_path) -> None:
    # Given
    state_file = tmp_path / "paused"
    store = PauseStore(state_file=state_file, owner_id="owner")

    # When
    result = store.handle(command="!pause-agents", actor_id="peer")

    # Then
    assert not result.accepted
    assert result.reason == "owner_required"
    assert not state_file.exists()


def test_delegation_when_query_is_received_then_response_preserves_correlation_id() -> None:
    # Given
    query = InteropEnvelope(
        correlation_id="corr-1",
        sender_id="agent-cha",
        recipient_id="peer-test",
        intent="query_availability",
        payload={"duration_min": 30},
    )

    # When
    response = response_for(query, sender_id="peer-test")
    parsed = parse_envelope(format_envelope(response))

    # Then
    assert parsed == response
    assert response.correlation_id == query.correlation_id
    assert response.recipient_id == query.sender_id
    assert response.intent == "response_availability"
