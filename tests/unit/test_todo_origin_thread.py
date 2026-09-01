"""RED-first contract: todo results (등록 완료/취소) go to the origin-channel thread.

Owner instruction 2026-08-23 (every approval-gated skill): the RESULT of an
approved action returns to the channel the instruction came from, in a thread;
the approval surface stays approval-only. todo had NO result notice at all —
only the verified receipt — so this adds both the success notice (after the
re-read proof) and the watcher's ⛔ notice, routed through the shared
``automation.interop.origin_notice`` helper. Fallback is the record's stored
approval channel (the watcher never resolves a fresh surface).
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "todo" / "scripts"))

import todo_approval  # noqa: E402
import todo_approval_runtime  # noqa: E402
import todo_approval_store_io as store_io  # noqa: E402
import todo_cli  # noqa: E402
import todo_confirm_reaction_watch as watch  # noqa: E402
from automation.interop.external_effect_gate import ApprovalContext  # noqa: E402
from todo_approval_model import ApprovalState, TodoApprovalSpec  # noqa: E402
from todo_approval_store import TodoApprovalStore  # noqa: E402

ORIGIN_CHANNEL = "200000000000000001"
ORIGIN_MESSAGE = "origin-message-1"
THREAD_ID = "300000000000000001"
APPROVAL_CHANNEL = "1526487935975952385"
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


def _spec(**overrides: object) -> TodoApprovalSpec:
    base: dict[str, object] = {
        "key": "todo:sha256:fixture",
        "action_hash": "sha256:fixture",
        "target_id": "tool:gws_tasks_mutation:gws",
        "argv_summary": "gws tasks tasks insert --params [masked] --json [masked]",
        "kind": "todo",
        "surface": "owner-dm",
        "channel_id": APPROVAL_CHANNEL,
        "policy_version": 7,
    }
    base.update(overrides)
    return TodoApprovalSpec(**base)  # type: ignore[arg-type]


def _origin_spec() -> TodoApprovalSpec:
    return _spec(origin_channel_id=ORIGIN_CHANNEL, origin_message_id=ORIGIN_MESSAGE)


class _SentChunk:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


class _FakeTransport:
    def __init__(self, *, thread_fail: bool = False) -> None:
        self.posts: list[tuple[str, str]] = []
        self.api_calls: list[tuple[str, str]] = []
        self.thread_fail = thread_fail

    def api(self, method: str, path: str, payload: dict | None = None):
        self.api_calls.append((method, path))
        if self.thread_fail:
            raise RuntimeError("thread api down")
        return {"id": THREAD_ID}

    def post_message(self, channel_id: str, content: str) -> str:
        self.posts.append((channel_id, content))
        return "dm-1"


def _thread_factory(log: list[tuple[str, str]]):
    class _ThreadTransport:
        def __init__(self, channel_id: str) -> None:
            self.channel_id = channel_id

        def send(self, body: str) -> tuple[_SentChunk, ...]:
            log.append((self.channel_id, body))
            return (_SentChunk("thread-post-1"),)

    return _ThreadTransport


# ------------------------------------------------------------ model / store

def test_spec_record_and_payload_carry_the_origin(tmp_path: Path) -> None:
    # Given: a channel-initiated approval spec
    store = TodoApprovalStore(tmp_path)
    # When: the pending generation is prepared
    record = store.prepare(_origin_spec(), NOW)
    # Then: record, durable payload and decode all carry the origin binding
    assert (record.origin_channel_id, record.origin_message_id) == (ORIGIN_CHANNEL, ORIGIN_MESSAGE)
    payload = json.loads(store.pending_path(record.key).read_text(encoding="utf-8"))
    assert payload["origin_channel_id"] == ORIGIN_CHANNEL
    assert payload["origin_message_id"] == ORIGIN_MESSAGE
    assert store_io.decode(json.dumps(payload)) == record


def test_legacy_payload_without_origin_decodes_to_empty(tmp_path: Path) -> None:
    # Given: a pre-origin pending payload on disk
    store = TodoApprovalStore(tmp_path)
    record = store.prepare(_spec(), NOW)
    payload = json.loads(store.pending_path(record.key).read_text(encoding="utf-8"))
    payload.pop("origin_channel_id", None)
    payload.pop("origin_message_id", None)
    # When: it is decoded
    decoded = store_io.decode(json.dumps(payload))
    # Then: the origin is empty, never guessed, and the record is otherwise intact
    assert (decoded.origin_channel_id, decoded.origin_message_id) == ("", "")
    assert decoded.action_hash == "sha256:fixture"


# -------------------------------------------------------------------- CLI

def test_parser_accepts_origin_flags_on_request_and_create() -> None:
    for command in ("request", "create", "plan"):
        args = todo_cli.build_parser().parse_args([
            command, "--title", "장보기",
            "--origin-channel-id", ORIGIN_CHANNEL, "--origin-message-id", ORIGIN_MESSAGE,
        ])
        assert (args.origin_channel_id, args.origin_message_id) == (ORIGIN_CHANNEL, ORIGIN_MESSAGE)


def test_request_intent_carries_the_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a channel instruction and a spied approval request
    captured: list[object] = []
    monkeypatch.setenv("TODO_OWNER_ID", "owner-1")
    monkeypatch.setattr(
        todo_cli, "evaluate",
        lambda argv, *, context: SimpleNamespace(action_hash="sha256:h", target_id="t"),
    )
    monkeypatch.setattr(
        todo_approval, "request_cli_approval", lambda intent, owner: captured.append(intent)
    )
    # When: the request command runs with origin flags
    assert todo_cli.main([
        "request", "--title", "장보기",
        "--origin-channel-id", ORIGIN_CHANNEL, "--origin-message-id", ORIGIN_MESSAGE,
    ]) == 0
    # Then: the intent (hence the spec/record) carries the origin
    [intent] = captured
    assert (intent.origin_channel_id, intent.origin_message_id) == (ORIGIN_CHANNEL, ORIGIN_MESSAGE)


# ----------------------------------------------------------- notify_result

def test_notify_result_routes_to_the_origin_thread() -> None:
    transport = _FakeTransport()
    posts: list[tuple[str, str]] = []
    todo_approval_runtime.notify_result(
        {
            "id": "fixture", "channel_id": APPROVAL_CHANNEL,
            "origin_channel_id": ORIGIN_CHANNEL, "origin_message_id": ORIGIN_MESSAGE,
        },
        "✅ 할일 등록 완료: 장보기 (task t-1)",
        thread_name="할일: 장보기",
        transport=transport,
        transport_factory=_thread_factory(posts),
    )
    assert transport.api_calls == [("POST", f"/channels/{ORIGIN_CHANNEL}/messages/{ORIGIN_MESSAGE}/threads")]
    assert posts == [(THREAD_ID, "✅ 할일 등록 완료: 장보기 (task t-1)")]
    assert transport.posts == []


def test_notify_result_falls_back_to_the_stored_channel_on_thread_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = _FakeTransport(thread_fail=True)
    posts: list[tuple[str, str]] = []
    todo_approval_runtime.notify_result(
        {
            "id": "fixture", "channel_id": APPROVAL_CHANNEL,
            "origin_channel_id": ORIGIN_CHANNEL, "origin_message_id": ORIGIN_MESSAGE,
        },
        "결과",
        thread_name="할일",
        transport=transport,
        transport_factory=_thread_factory(posts),
    )
    assert posts == []
    assert transport.posts == [(APPROVAL_CHANNEL, "결과")]
    assert "NOTIFY-THREAD-FAIL" in capsys.readouterr().err


def test_notify_result_without_origin_posts_to_the_stored_channel() -> None:
    transport = _FakeTransport()
    todo_approval_runtime.notify_result(
        {"id": "fixture", "channel_id": APPROVAL_CHANNEL},
        "결과",
        thread_name="할일",
        transport=transport,
        transport_factory=_thread_factory([]),
    )
    assert transport.api_calls == []
    assert transport.posts == [(APPROVAL_CHANNEL, "결과")]


# ------------------------------------------------------- create-path notice

def _context(*, e2e: bool = False) -> ApprovalContext:
    return ApprovalContext(approval_log=None, owner_id="owner-1", e2e_test_mode=e2e)


def test_create_notice_uses_the_approved_record_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the ✅-archived approval generation that carries the origin
    monkeypatch.setenv("TODO_APPROVAL_ROOT", str(tmp_path))
    store = TodoApprovalStore(tmp_path)
    record = store.bind_message(store.prepare(_origin_spec(), NOW), "m-1")
    store.archive(record, ApprovalState.ARCHIVED, "approved")
    captured: list[tuple[dict, str]] = []
    monkeypatch.setattr(
        todo_approval_runtime, "notify_result",
        lambda record_like, content, **_kw: captured.append((record_like, content)),
    )
    # When: the verified write reports its result
    todo_cli._notify_created("sha256:fixture", "task-1", "장보기", _context())
    # Then: the notice is routed by that record's origin and names the task
    [(record_like, content)] = captured
    assert record_like["origin_channel_id"] == ORIGIN_CHANNEL
    assert record_like["origin_message_id"] == ORIGIN_MESSAGE
    assert record_like["channel_id"] == APPROVAL_CHANNEL
    assert "등록 완료" in content and "장보기" in content and "task-1" in content


def test_create_notice_is_skipped_under_e2e_approvals(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        todo_approval_runtime, "notify_result",
        lambda *a, **k: pytest.fail("e2e approvals must not open a real notice"),
    )
    todo_cli._notify_created("sha256:fixture", "task-1", "장보기", _context(e2e=True))
    assert "NOTIFY-SKIP" in capsys.readouterr().err


def test_create_notice_failure_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TODO_APPROVAL_ROOT", str(tmp_path))

    def boom(*_a, **_k):
        raise RuntimeError("discord down")

    monkeypatch.setattr(todo_approval_runtime, "notify_result", boom)
    todo_cli._notify_created("sha256:missing", "task-1", "장보기", _context())
    assert "NOTIFY-FAIL" in capsys.readouterr().err


# -------------------------------------------------------- watcher ⛔ notice

def test_watch_decision_notifies_after_cancel_archive(tmp_path: Path) -> None:
    # Given: a bound pending generation and a spied notifier
    store = TodoApprovalStore(tmp_path)
    record = store.bind_message(store.prepare(_origin_spec(), NOW), "m-1")
    notified: list[object] = []
    decision = watch.TodoOwnerDecision(
        record, None, store, tmp_path / "approvals.jsonl", "owner-1", NOW,
        lambda *_a: True, notify=notified.append,
    )
    probe = todo_approval.lifecycle().Probe
    # When: the owner cancels and the decision is dropped
    decision.apply(None, probe.CANCELLED)
    decision.drop(watch._request(record))
    # Then: the generation is archived as cancelled and the notice fired once
    assert store.active(record.key) is None
    assert [item.outcome for item in store.archives(record.key)] == ["cancelled"]
    assert notified == [record]


def test_watch_cancel_notice_failure_keeps_the_archive(tmp_path: Path, capsys) -> None:
    store = TodoApprovalStore(tmp_path)
    record = store.bind_message(store.prepare(_origin_spec(), NOW), "m-1")

    def boom(_record):
        raise RuntimeError("discord down")

    decision = watch.TodoOwnerDecision(
        record, None, store, tmp_path / "approvals.jsonl", "owner-1", NOW,
        lambda *_a: True, notify=boom,
    )
    decision.apply(None, todo_approval.lifecycle().Probe.CANCELLED)
    decision.drop(watch._request(record))
    assert [item.outcome for item in store.archives(record.key)] == ["cancelled"]
    assert "NOTIFY-FAIL" in capsys.readouterr().err


def test_watch_cancel_notice_routes_to_the_origin_thread(tmp_path: Path) -> None:
    store = TodoApprovalStore(tmp_path)
    record = store.bind_message(store.prepare(_origin_spec(), NOW), "m-1")
    transport = _FakeTransport()
    posts: list[tuple[str, str]] = []
    watch._notify_cancelled(record, transport, transport_factory=_thread_factory(posts))
    assert transport.api_calls == [("POST", f"/channels/{ORIGIN_CHANNEL}/messages/{ORIGIN_MESSAGE}/threads")]
    assert [channel for channel, _ in posts] == [THREAD_ID]
    assert "할일 등록 취소" in posts[0][1]
    assert transport.posts == []


def test_notify_result_falls_back_to_the_stored_channel_when_helper_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = _FakeTransport()

    def missing(name: str):
        raise ImportError(f"No module named 'automation.interop.{name}'")

    monkeypatch.setattr(todo_approval_runtime, "_repo_module", missing)
    todo_approval_runtime.notify_result(
        {
            "id": "fixture", "channel_id": APPROVAL_CHANNEL,
            "origin_channel_id": ORIGIN_CHANNEL, "origin_message_id": ORIGIN_MESSAGE,
        },
        "결과",
        thread_name="할일",
        transport=transport,
        transport_factory=_thread_factory([]),
    )
    assert transport.posts == [(APPROVAL_CHANNEL, "결과")]
    assert "NOTIFY-HELPER-MISSING" in capsys.readouterr().err
