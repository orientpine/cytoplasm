from __future__ import annotations

import sys
from datetime import datetime, timezone
from urllib.error import URLError
from importlib import import_module
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))

calendar_confirm = import_module("calendar_confirm")
calendar_card = import_module("calendar_card")
calendar_approval = import_module("calendar_approval")
calendar_core = import_module("calendar_core")
calendar_gate = import_module("calendar_gate")
from automation.interop import approval_lifecycle, approval_surface  # noqa: E402
from tests.unit.test_calendar_single_live_request import (  # noqa: E402
    OWNER, REQUEST_THREAD_ID, FakeDiscord, calendar_cli,
    calendar_env as calendar_env,
)

TITLE = "Synthetic appointment A"
START = "2031-10-01T09:00:00+09:00"
END = "2031-10-01T10:00:00+09:00"
START_DISPLAY = "2031-10-01 09:00 (+09:00)"
END_DISPLAY = "2031-10-01 10:00 (+09:00)"


@pytest.fixture
def detail_flow(calendar_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake, _calls = calendar_env
    monkeypatch.setenv("CALENDAR_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    event = {"id": "event-1", "summary": TITLE,
             "start": {"dateTime": START}, "end": {"dateTime": END}}
    monkeypatch.setattr(calendar_cli.calendar_preflight, "read_event", lambda *_args: event)
    # Time is not under test; explicit dates remain deterministic across runs.
    class Clock:
        @staticmethod
        def now(_tz=None):
            return datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(calendar_cli, "datetime", Clock)
    return fake


def _new_draft(arguments: list[str]):
    args = calendar_cli.build_parser().parse_args(arguments)
    assert args.func(args) == 0
    return calendar_gate.list_drafts()[-1]


@pytest.mark.parametrize(("arguments", "values"), [
    (["draft-create", "--text", "2031-10-01 09:00 meeting 1시간", "--summary", TITLE],
     (TITLE, START_DISPLAY, END_DISPLAY)),
    (["draft-update", "--event-id", "event-1", "--summary", "Synthetic changed title"],
     ("Synthetic changed title", START_DISPLAY, END_DISPLAY)),
    (["draft-update", "--event-id", "event-1", "--text", "2031-10-02 11:00 not-a-title 1시간"],
     (TITLE, "2031-10-02 11:00 (+09:00)", "2031-10-02 12:00 (+09:00)")),
    (["draft-delete", "--event-id", "event-1", "--label", "untrusted caller label"],
     (TITLE, START_DISPLAY, END_DISPLAY)),
])
def test_private_card_carries_frozen_change_details_when_produced(
    detail_flow, arguments: list[str], values: tuple[str, str, str],
) -> None:
    # Given: an actual CLI-produced draft with a read-only event fixture.
    draft = _new_draft(arguments)
    before = draft["sha256"]
    # When: the real lifecycle and directory publish its approval.
    entry = calendar_approval.request_confirmation(draft)
    # Then: the private card carries exact selected data, not caller hints.
    content = detail_flow.contents[entry.dm_message_id]
    assert all(value in content for value in values)
    assert "not-a-title" not in content
    assert "untrusted caller label" not in content
    assert entry.render_version == "4"
    stored = calendar_gate.load_draft(draft["id"])
    assert calendar_core.draft_sha256(stored) == before == entry.sha256
    assert not any(value in name for value in values for name in detail_flow.request_threads)


@pytest.mark.parametrize("surface", ["shared", "other-thread", "unknown"])
def test_producer_refuses_before_post_when_destination_changes_after_resolution(
    detail_flow: FakeDiscord, monkeypatch: pytest.MonkeyPatch, surface: str,
) -> None:
    # Given: resolution initially sees the configured thread, but POST-time facts differ.
    draft = _new_draft(["draft-create", "--text", "2031-10-01 09:00 meeting", "--summary", TITLE])
    reads = 0
    def api(method, path, payload=None):
        nonlocal reads
        if method == "GET" and path == f"/channels/{REQUEST_THREAD_ID}":
            reads += 1
            if reads > 1:
                if surface == "unknown":
                    raise URLError("offline channel unavailable")
                return {"id": REQUEST_THREAD_ID, "type": 0 if surface == "shared" else 11,
                        "name": "shared", "parent_id": "999"}
        return detail_flow(method, path, payload)
    monkeypatch.setattr(calendar_confirm, "_api", api)
    # When: posting through the actual producer.
    with pytest.raises((approval_lifecycle.ApprovalSurfaceError, calendar_gate.GateError)):
        calendar_approval.request_confirmation(draft)
    # Then: no approval content is sent to the changed/unknown destination.
    assert detail_flow.posts == 0


def test_post_refuses_before_sending_when_intent_disagrees_with_binding(detail_flow) -> None:
    # Given: the caller names a different channel than its explicit private binding.
    draft = _new_draft(["draft-create", "--text", "2031-10-01 09:00 meeting", "--summary", TITLE])
    binding = approval_surface.ApprovalBinding(
        approval_surface.ApprovalKind.CALENDAR, approval_surface.ApprovalSurface.AGENT_CHAT_THREAD,
        REQUEST_THREAD_ID, approval_surface.POLICY_VERSION,
    )
    gate = calendar_approval.CalendarApprovalGate(draft, calendar_approval.PendingConfirmStore(), OWNER, binding)
    intent = approval_lifecycle.ApprovalIntent(calendar_approval.approval_key(draft), draft["sha256"], "999")
    # When / Then: binding validation precedes the HTTP POST, not only commit.
    with pytest.raises(approval_lifecycle.ApprovalSurfaceError):
        gate.post(intent)
    assert detail_flow.posts == 0


def test_long_private_card_is_refused_before_thread_or_journal_effects(detail_flow) -> None:
    # Given: the new card would exceed Discord's bound once its title is included.
    draft = _new_draft(["draft-create", "--text", "2031-10-01 09:00 meeting", "--summary", "X" * 2100])
    # When / Then: preparation fails before any notice, thread, post or reservation.
    with pytest.raises(calendar_gate.GateError):
        calendar_approval.request_confirmation(draft)
    assert detail_flow.posts == 0
    assert detail_flow.request_threads == []
    assert detail_flow.notice_ids == []
    assert calendar_approval.posting_journal().outstanding(calendar_approval.approval_key(draft)) is None


@pytest.mark.parametrize("event", [
    None, {"id": "other"}, {"id": "event-1", "summary": TITLE},
    {"id": "event-1", "summary": TITLE, "start": {"dateTime": "invalid"}, "end": {"dateTime": END}},
    {"id": "event-1", "summary": TITLE, "start": {"dateTime": "2031-10-01T09:00:00"}, "end": {"dateTime": END}},
])
def test_missing_event_context_refuses_new_delete_before_persistence(detail_flow, monkeypatch, event) -> None:
    # Given: the read-only lookup cannot provide the requested event's complete context.
    monkeypatch.setattr(calendar_cli.calendar_preflight, "read_event", lambda *_args: event)
    # When / Then: no incomplete draft is left for a watcher to publish later.
    with pytest.raises(calendar_gate.GateError):
        _new_draft(["draft-delete", "--event-id", "event-1"])
    assert calendar_gate.list_drafts() == []
    assert detail_flow.posts == 0


def test_captured_context_is_replayed_without_requery_or_mutation_changes(detail_flow, monkeypatch) -> None:
    # Given: the event context has been read and frozen into a deletion draft.
    draft = _new_draft(["draft-delete", "--event-id", "event-1"])
    mutation = calendar_core.external_effect_action_hash(tuple(draft["argv"]))
    def forbidden(*_args):
        pytest.fail("posting must use frozen event context")
    monkeypatch.setattr(calendar_cli.calendar_preflight, "read_event", forbidden)
    # When: posting later, independently of live event changes.
    entry = calendar_approval.request_confirmation(draft)
    # Then: the card retains the captured values and the exact original mutation.
    assert all(
        value in detail_flow.contents[entry.dm_message_id]
        for value in (TITLE, START_DISPLAY, END_DISPLAY)
    )
    assert calendar_core.external_effect_action_hash(tuple(calendar_gate.load_draft(draft["id"])["argv"])) == mutation


@pytest.mark.parametrize("case", [
    (({"dateTime": "2031-10-01T00:00:00Z"}, {"dateTime": "2031-10-01T01:00:00Z"}), (START, END)),
    (({"dateTime": "2031-10-01T09:00:00", "timeZone": "Asia/Seoul"},
      {"dateTime": "2031-10-01T10:00:00", "timeZone": "Asia/Seoul"}), (START, END)),
    (({"date": "2031-10-01"}, {"date": "2031-10-02"}), ("2031-10-01", "2031-10-02")),
])
def test_event_times_keep_their_meaning_when_frozen_for_display(detail_flow, monkeypatch, case) -> None:
    # Given: a UTC, named-zone, or all-day event from the existing read boundary.
    times, expected = case
    monkeypatch.setattr(calendar_cli.calendar_preflight, "read_event", lambda *_args: {
        "id": "event-1", "summary": TITLE, "start": times[0], "end": times[1],
    })
    # When: the actual draft producer captures its display context.
    draft = _new_draft(["draft-delete", "--event-id", "event-1"])
    # Then: timed values are explicit KST and dates retain their exclusive end date.
    assert (draft["start"], draft["end"]) == expected
    assert calendar_core.draft_sha256(draft) == draft["sha256"]


def test_tampered_context_is_rejected_before_new_approval(detail_flow) -> None:
    # Given: stored context has changed without updating the frozen draft hash.
    draft = _new_draft(["draft-delete", "--event-id", "event-1"])
    draft["summary"] = "different captured event"
    # When / Then: no owner is asked to approve mismatched context.
    with pytest.raises(calendar_gate.GateError):
        calendar_approval.request_confirmation(draft)
    assert detail_flow.posts == 0
    assert detail_flow.request_threads == []


@pytest.mark.parametrize("body", ["{}", '{"summary":null}', '{"start":null}'])
def test_unsupported_patch_fields_are_not_presented_as_unchanged(detail_flow, body: str) -> None:
    # Given: a hash-valid command outside the CLI's supported patch contract.
    draft = _new_draft(["draft-update", "--event-id", "event-1", "--summary", TITLE])
    draft["argv"][draft["argv"].index("--json") + 1] = body
    draft["sha256"] = calendar_core.draft_sha256(draft)
    # When / Then: an empty patch or explicit null cannot masquerade as field retention.
    with pytest.raises(calendar_gate.GateError):
        calendar_approval.request_confirmation(draft)
    assert detail_flow.posts == 0


@pytest.mark.parametrize("version", ["1", "2"])
def test_old_card_bytes_and_pending_replay_are_unchanged(detail_flow, monkeypatch, version: str) -> None:
    # Given: the independently captured old posted-copy contract, not new wording.
    from tests.unit.test_mail_approval_cards import LEGACY, record
    from tests.unit.mail_approval_card_golden import BYTES
    draft = {**record(), "render_version": version, "status": "pending"}
    expected = LEGACY["calendar"] if version == "1" else BYTES["calendar"]
    entry = calendar_approval.request_confirmation(draft)
    assert detail_flow.contents[entry.dm_message_id] == expected
    def forbidden(_draft):
        pytest.fail("an intact stored approval must not be re-rendered")
    monkeypatch.setattr(calendar_confirm, "render_confirmation", forbidden)
    # When: requesting the same pending approval again.
    replay = calendar_approval.request_confirmation(calendar_gate.load_draft(draft["id"]))
    # Then: message id, version, hash and bytes remain the original approval.
    assert replay == entry
    assert replay.render_version == version
    assert detail_flow.posts == 1
    assert detail_flow.contents[entry.dm_message_id] == expected


def test_confirmation_message_instructs_owner_to_react_on_that_dm_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    messages: list[str] = []

    def fake_api(method: str, path: str, payload: dict[str, str] | None = None):
        if method == "POST" and path == "/channels/dm123/messages":
            assert payload is not None
            messages.append(payload["content"])
            return {"id": "msg123"}
        return None

    draft = {
        "id": "draft123",
        "sha256": "hash123",
    }
    monkeypatch.setattr(calendar_confirm, "_change_summary", lambda _draft: "CHANGE-SUMMARY")
    monkeypatch.setattr(calendar_confirm, "_api", fake_api)

    # When
    calendar_confirm.post_confirmation_message(draft, "dm123")

    # Then
    assert "이 메시지에 ✅ 실행 / ⛔ 취소" in messages[0]
    assert calendar_confirm.APPROVE_EMOJI in messages[0]
    assert calendar_confirm.CANCEL_EMOJI in messages[0]
    assert "approvals" not in messages[0]
