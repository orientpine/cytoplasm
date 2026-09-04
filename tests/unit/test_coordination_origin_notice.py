"""Coordination RESULT notices go back to the origin channel's thread.

소유자 지시 2026-08-23: 승인 표면(✅/⛔)은 승인 전용으로 남기고, 완료·취소·만료
결과는 지시가 시작된 채널의 스레드로 돌려보낸다. 라우팅·폴백·NOTIFY-THREAD-FAIL
의미는 공유 구현 ``automation.interop.origin_notice.deliver``가 소유한다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "coordination" / "scripts"))

import confirm_reaction_watch as watch  # noqa: E402
import coordinate_cli  # noqa: E402
import coordinate_io as io  # noqa: E402
import coordination_lifecycle as lifecycle  # noqa: E402
from automation.interop import coordination  # noqa: E402
from coordination_pending import PendingConfirm, PendingConfirmStore  # noqa: E402

ORIGIN_CHANNEL = "origin-chan-1"
ORIGIN_MESSAGE = "origin-msg-1"
THREAD_ID = "thread-9"
OWNER = "cha-owner"
SLOT = "2026-07-18T09:00:00+09:00"


class _SentChunk:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


@dataclass
class _ThreadSpy:
    """Stand-in for the runtime Discord transport bound to one thread."""

    posts: list[tuple[str, str]] = field(default_factory=list)

    def factory(self, channel_id: str) -> _ThreadSpy.Bound:
        return _ThreadSpy.Bound(self, channel_id)

    @dataclass
    class Bound:
        spy: _ThreadSpy
        channel_id: str

        def send(self, content: str) -> tuple[_SentChunk, ...]:
            self.spy.posts.append((self.channel_id, content))
            return (_SentChunk("thread-post-1"),)


@dataclass
class FakeDiscord:
    """The watcher's owner-DM surface — the fallback leg, never the thread leg."""

    reactions: dict[str, tuple[dict[str, str | bool], ...]]
    sent_messages: list[str] = field(default_factory=list)

    def message_content(self, entry: PendingConfirm) -> str:
        return f"coordination confirmation sha256:{entry.sha256}"

    def reaction_users(
        self, _entry: PendingConfirm, emoji: str
    ) -> tuple[dict[str, str | bool], ...]:
        return self.reactions.get(emoji, ())

    def send_owner_dm(self, content: str) -> None:
        self.sent_messages.append(content)


@dataclass
class FakeCommands:
    finalized: list[PendingConfirm] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def finalize(self, entry: PendingConfirm) -> None:
        self.finalized.append(entry)

    def discard(self, draft_id: str) -> None:
        self.discarded.append(draft_id)


def _entry(*, origin: bool = True) -> PendingConfirm:
    return PendingConfirm(
        draft_id="abc123",
        sha256="sha-123",
        dm_channel_id="dm-1",
        dm_message_id="msg-1",
        slot=SLOT,
        summary="피어 미팅",
        correlation="coord-123",
        duration_min=30,
        created=datetime(2026, 7, 17, tzinfo=UTC),
        origin_channel_id=ORIGIN_CHANNEL if origin else "",
        origin_message_id=ORIGIN_MESSAGE if origin else "",
    )


def _thread_api(created: list[dict | None], *, fail: bool = False):
    def api(method: str, path: str, payload: dict | None = None):
        if method == "POST" and path == (
            f"/channels/{ORIGIN_CHANNEL}/messages/{ORIGIN_MESSAGE}/threads"
        ):
            if fail:
                raise RuntimeError("thread API down")
            created.append(payload)
            return {"id": THREAD_ID}
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    return api


def _executed_commands() -> tuple[coordination.Command, ...]:
    state, _ = coordination.on_owner_confirm(
        coordination.CoordinationState(
            phase=coordination.Phase.AWAIT_OWNER_CONFIRM, candidates=(SLOT,)
        ),
        True,
    )
    _, commands = coordination.on_executed(state)
    return commands


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTEROP_RUNTIME", str(_REPO))


def test_pending_record_round_trips_the_origin_binding(tmp_path: Path) -> None:
    # Given: a channel-instructed coordination approval is stored
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")

    # When: the pending record is written and read back
    store.append(_entry())
    [loaded] = store.load()

    # Then: the origin binding survives for result routing
    assert loaded.origin_channel_id == ORIGIN_CHANNEL
    assert loaded.origin_message_id == ORIGIN_MESSAGE


def test_legacy_pending_row_without_origin_stays_readable(tmp_path: Path) -> None:
    # Given: a row written before the origin fields existed
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "correlation": "coord-legacy", "created": "2026-07-17T00:00:00Z",
        "dm_channel_id": "dm-1", "dm_message_id": "legacy-msg", "draft_id": "abc123",
        "duration_min": 30, "sha256": "sha-123", "slot": SLOT, "summary": "피어 미팅",
    }
    store.path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    # When: the watcher loads the store
    [loaded] = store.load()

    # Then: it reads with an empty binding instead of refusing the row
    assert (loaded.origin_channel_id, loaded.origin_message_id) == ("", "")


def test_request_parser_accepts_origin_flags() -> None:
    # Given / When: a request is parsed with the instruction's origin refs
    args = coordinate_cli.build_parser().parse_args([
        "request", "--peer", "peer-test", "--summary", "피어 미팅",
        "--origin-channel-id", ORIGIN_CHANNEL, "--origin-message-id", ORIGIN_MESSAGE,
    ])

    # Then: the namespace carries them into the owner leg
    assert args.origin_channel_id == ORIGIN_CHANNEL
    assert args.origin_message_id == ORIGIN_MESSAGE


@pytest.mark.usefixtures("runtime")
def test_finish_posts_the_completion_result_to_the_origin_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an executed coordination whose instruction came from a channel
    spy = _ThreadSpy()
    created: list[dict | None] = []
    team_posts: list[tuple[str, str]] = []
    dm_notices: list[tuple[str, str]] = []
    monkeypatch.setattr(io, "api", _thread_api(created))
    monkeypatch.setattr(io, "team_channel_id", lambda: "team-chan")
    monkeypatch.setattr(
        io, "post_message",
        lambda channel_id, content: (team_posts.append((channel_id, content)), "msg-9")[1],
    )
    monkeypatch.setattr(
        lifecycle, "send_owner_dm",
        lambda owner_id, content: (dm_notices.append((owner_id, content)), ("dm", "m"))[1],
    )
    monkeypatch.setattr(lifecycle, "_thread_transport", spy.factory)

    # When: the lifecycle finishes the run
    exit_code = lifecycle.finish(
        {"agent_id": "agent-me", "owner_id": OWNER}, "coord-test123", _executed_commands(),
        "2026-07-18 (토) 09:00~09:30 KST", "피어 미팅", "event-abc123",
        record={
            "id": "abc123",
            "origin_channel_id": ORIGIN_CHANNEL,
            "origin_message_id": ORIGIN_MESSAGE,
        },
    )

    # Then: the result went to the origin thread and no owner DM was opened
    assert exit_code == 0
    assert len(created) == 1
    assert spy.posts == [(
        THREAD_ID,
        "✅ 일정 조율 완료 (coord-test123): 피어 미팅 — "
        "2026-07-18 (토) 09:00~09:30 KST. 캘린더에 등록되었습니다.",
    )]
    assert dm_notices == []
    # …and the #team notice is unchanged by the routing switch
    assert [channel for channel, _content in team_posts] == ["team-chan"]


@pytest.mark.usefixtures("runtime")
def test_finish_without_origin_still_uses_the_owner_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a run with no recorded origin (legacy record or manual finalize)
    spy = _ThreadSpy()
    dm_notices: list[tuple[str, str]] = []
    monkeypatch.setattr(io, "team_channel_id", lambda: "team-chan")
    monkeypatch.setattr(io, "post_message", lambda _channel_id, _content: "msg-9")
    monkeypatch.setattr(
        lifecycle, "send_owner_dm",
        lambda owner_id, content: (dm_notices.append((owner_id, content)), ("dm", "m"))[1],
    )
    monkeypatch.setattr(lifecycle, "_thread_transport", spy.factory)

    # When: the lifecycle finishes the run
    exit_code = lifecycle.finish(
        {"agent_id": "agent-me", "owner_id": OWNER}, "coord-test123", _executed_commands(),
        "2026-07-18 (토) 09:00~09:30 KST", "피어 미팅", "event-abc123",
    )

    # Then: the established owner notice path is untouched
    assert exit_code == 0
    assert spy.posts == []
    assert [owner for owner, _content in dm_notices] == [OWNER]
    assert dm_notices[0][1].startswith("✅ 일정 조율 완료 (coord-test123)")


def _run_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reactions: dict[str, tuple[dict[str, str | bool], ...]],
    *,
    now: datetime,
    thread_fail: bool = False,
) -> tuple[FakeDiscord, FakeCommands, _ThreadSpy, list[dict | None]]:
    monkeypatch.setenv("INTEROP_RUNTIME", str(_REPO))
    spy = _ThreadSpy()
    created: list[dict | None] = []
    monkeypatch.setattr(io, "api", _thread_api(created, fail=thread_fail))
    monkeypatch.setattr(lifecycle, "_thread_transport", spy.factory)
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    store.append(_entry())
    discord = FakeDiscord(reactions)
    commands = FakeCommands()
    watch.run_once(
        store=store, owner_id=OWNER, discord=discord, commands=commands,
        draft_sha256=lambda _draft_id: "sha-123", now=now,
    )
    return discord, commands, spy, created


def test_watcher_cancel_result_goes_to_the_origin_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an origin-bound pending confirmation the owner cancels with ⛔
    discord, commands, spy, created = _run_watch(
        tmp_path, monkeypatch, {"⛔": ({"id": OWNER, "bot": False},)},
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )

    # Then: the discard is reported in the origin thread, never in the owner DM
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == []
    assert len(created) == 1
    assert spy.posts == [(
        THREAD_ID,
        "⛔ 일정 조율 취소 (draft abc123) — 소유자 ⛔ 리액션으로 취소되었습니다.",
    )]


def test_watcher_expiry_result_goes_to_the_origin_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an origin-bound pending confirmation that outlived its 24h window
    discord, commands, spy, _created = _run_watch(
        tmp_path, monkeypatch, {}, now=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )

    # Then: the expiry cancellation is reported in the origin thread
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == []
    assert spy.posts == [(
        THREAD_ID,
        "⌛ 일정 조율 만료 취소 (draft abc123) — 확정 시간이 지나 취소되었습니다.",
    )]


def test_watcher_falls_back_to_owner_notice_when_the_thread_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an origin-bound ⛔ whose thread creation will fail
    discord, commands, spy, _created = _run_watch(
        tmp_path, monkeypatch, {"⛔": ({"id": OWNER, "bot": False},)},
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC), thread_fail=True,
    )

    # Then: the committed discard still reaches the owner, with the failure marker
    assert commands.discarded == ["abc123"]
    assert spy.posts == []
    assert discord.sent_messages == [
        "⛔ 일정 조율 취소 (draft abc123) — 소유자 ⛔ 리액션으로 취소되었습니다."
    ]
    assert "NOTIFY-THREAD-FAIL" in capsys.readouterr().err


# ------------------------------------------- per-request approval thread closing

APPROVAL_THREAD = "300000000000000091"
APPROVAL_THREAD_NAME = "일정 조율 · coord-123"


def _approval_thread_api(patches: list[dict]):
    """Serve only what closing an existing approval thread needs — never /threads."""

    def api(method: str, path: str, payload: dict | None = None):
        if method == "GET" and path == f"/channels/{APPROVAL_THREAD}":
            return {"id": APPROVAL_THREAD, "name": APPROVAL_THREAD_NAME}
        if method == "PATCH" and path == f"/channels/{APPROVAL_THREAD}":
            patches.append(dict(payload or {}))
            return {"id": APPROVAL_THREAD}
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    return api


@pytest.mark.usefixtures("runtime")
def test_completion_result_closes_the_request_thread_it_was_approved_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an executed coordination approved in its own request thread
    spy = _ThreadSpy()
    patches: list[dict] = []
    team_posts: list[tuple[str, str]] = []
    dm_notices: list[tuple[str, str]] = []
    monkeypatch.setattr(io, "api", _approval_thread_api(patches))
    monkeypatch.setattr(io, "team_channel_id", lambda: "team-chan")
    monkeypatch.setattr(
        io, "post_message",
        lambda channel_id, content: (team_posts.append((channel_id, content)), "msg-9")[1],
    )
    monkeypatch.setattr(
        lifecycle, "send_owner_dm",
        lambda owner_id, content: (dm_notices.append((owner_id, content)), ("dm", "m"))[1],
    )
    monkeypatch.setattr(lifecycle, "_thread_transport", spy.factory)

    # When: the lifecycle finishes the run
    exit_code = lifecycle.finish(
        {"agent_id": "agent-me", "owner_id": OWNER}, "coord-test123", _executed_commands(),
        "2026-07-18 (토) 09:00~09:30 KST", "피어 미팅", "event-abc123",
        record={
            "id": "abc123",
            "approval_thread_id": APPROVAL_THREAD,
            "origin_channel_id": ORIGIN_CHANNEL,
            "origin_message_id": ORIGIN_MESSAGE,
        },
    )

    # Then: the completion lands in the approval thread, which then closes as done
    assert exit_code == 0
    assert [channel for channel, _content in spy.posts] == [APPROVAL_THREAD]
    assert patches == [{"archived": True, "name": f"✅ 완료 · {APPROVAL_THREAD_NAME}"}]
    assert dm_notices == []
    # …and the #team notice is unchanged by the thread closing
    assert [channel for channel, _content in team_posts] == ["team-chan"]


def _run_approval_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reactions: dict[str, tuple[dict[str, str | bool], ...]],
    *,
    now: datetime,
) -> tuple[FakeDiscord, FakeCommands, _ThreadSpy, list[dict]]:
    monkeypatch.setenv("INTEROP_RUNTIME", str(_REPO))
    spy = _ThreadSpy()
    patches: list[dict] = []
    monkeypatch.setattr(io, "api", _approval_thread_api(patches))
    monkeypatch.setattr(lifecycle, "_thread_transport", spy.factory)
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    entry = _entry()
    store.append(
        PendingConfirm(
            draft_id=entry.draft_id, sha256=entry.sha256, dm_channel_id=entry.dm_channel_id,
            dm_message_id=entry.dm_message_id, slot=entry.slot, summary=entry.summary,
            correlation=entry.correlation, duration_min=entry.duration_min,
            created=entry.created, origin_channel_id=entry.origin_channel_id,
            origin_message_id=entry.origin_message_id, approval_thread_id=APPROVAL_THREAD,
        )
    )
    discord = FakeDiscord(reactions)
    commands = FakeCommands()
    watch.run_once(
        store=store, owner_id=OWNER, discord=discord, commands=commands,
        draft_sha256=lambda _draft_id: "sha-123", now=now,
    )
    return discord, commands, spy, patches


def test_watcher_cancel_closes_the_request_thread_as_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an approval-thread-bound pending confirmation the owner cancels with ⛔
    discord, commands, spy, patches = _run_approval_watch(
        tmp_path, monkeypatch, {"⛔": ({"id": OWNER, "bot": False},)},
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )

    # Then: the notice lands in the approval thread, which closes as cancelled
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == []
    assert [channel for channel, _content in spy.posts] == [APPROVAL_THREAD]
    assert patches == [{"archived": True, "name": f"⛔ 취소 · {APPROVAL_THREAD_NAME}"}]


def test_watcher_expiry_closes_the_request_thread_as_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an approval-thread-bound pending confirmation that outlived its window
    discord, commands, spy, patches = _run_approval_watch(
        tmp_path, monkeypatch, {}, now=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )

    # Then: the expiry notice closes the same thread
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == []
    assert [channel for channel, _content in spy.posts] == [APPROVAL_THREAD]
    assert patches == [{"archived": True, "name": f"⌛ 만료 · {APPROVAL_THREAD_NAME}"}]


def test_notify_result_falls_back_to_caller_when_helper_is_unavailable(monkeypatch, capsys):
    # Given: the interop runtime lacks origin_notice (stale runtime / sandbox)
    import coordination_lifecycle

    fallback_log: list[str] = []

    def missing():
        raise ImportError("No module named 'automation'")

    monkeypatch.setattr(coordination_lifecycle, "_origin_notice", missing)
    # When: an origin-bound result is delivered
    coordination_lifecycle.notify_result(
        {"id": "abc123", "origin_channel_id": "200000000000000001", "origin_message_id": "m-1"},
        "결과",
        fallback=lambda content: fallback_log.append(content) or "dm-1",
    )
    # Then: the caller's fallback still fires, with a marker
    assert fallback_log == ["결과"]
    assert "NOTIFY-HELPER-MISSING" in capsys.readouterr().err
