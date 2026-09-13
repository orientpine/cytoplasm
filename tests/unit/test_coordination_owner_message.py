"""Coordination result envelopes and unchanged inter-agent protocol bytes."""
from __future__ import annotations

import builtins
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills/calendar/scripts"))
sys.path.insert(0, str(_REPO / "skills/coordination/scripts"))

import coordination_lifecycle as lifecycle  # noqa: E402
from automation.interop import coordination, origin_notice  # noqa: E402
from coordination_pending import PendingConfirm, PendingConfirmStore  # noqa: E402

CONTENT = "✅ 일정 조율 완료 (coord-test): 피어 미팅 — 슬롯. 캘린더에 등록되었습니다."
CLI_THREAD_POSTED = (
    "대상: 피어 미팅 (abc123)\n"
    "사실: ✅ 일정 조율 완료 (coord-test): 피어 미팅 — 2026-07-18 (토) 09:00~09:30 KST. 캘린더에 등록되었습니다. (실행 완료)\n"
    "위치: 여기\n인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n되돌리기: 해당 없음"
)
CLI_FALLBACK_POSTED = (
    "대상: 피어 미팅 (abc123)\n"
    "사실: ✅ 일정 조율 완료 (coord-test): 피어 미팅 — 2026-07-18 (토) 09:00~09:30 KST. 캘린더에 등록되었습니다. (실행 완료)\n"
    "위치: https://discord.com/channels/111/222/333\n"
    "인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n되돌리기: 해당 없음"
)


def pending() -> PendingConfirm:
    return PendingConfirm(
        draft_id="abc123", sha256="hash", dm_channel_id="222", dm_message_id="333",
        slot="2026-07-18T09:00:00+09:00", summary="피어 미팅", correlation="coord-test",
        duration_min=30, created=datetime(2026, 7, 17, tzinfo=UTC),
        approval_thread_id="222", approval_guild_id="111", origin_message_id="444",
    )


def test_team_notice_bytes_when_coordination_completes() -> None:
    # Given: the protocol correlation and time label (not an owner prompt).
    correlation, label = "coord-test", "2026-07-18 09:00~09:30 KST"
    # When: the peer-facing notice is rendered.
    result = coordination.team_notice(correlation, label)
    # Then: bot parsers retain the exact shipped bytes and prefix.
    assert coordination.CORRELATION_PREFIX.encode() == b"coord-"
    assert result.encode() == (
        "📅 일정 확정 (coord-test): 2026-07-18 09:00~09:30 KST — 양측 승인 완료."
    ).encode()
    print(repr(result), repr(coordination.CORRELATION_PREFIX))


@pytest.mark.parametrize("runtime", ["missing_module", "old_signature"])
def test_legacy_bytes_when_runtime_predates_envelopes(monkeypatch, runtime) -> None:
    # Given: either the optional module is absent or the old facade signature remains.
    sent: list[str] = []
    monkeypatch.setattr(lifecycle, "_origin_notice", lambda: origin_notice)
    if runtime == "missing_module":
        original_import = builtins.__import__

        def importing(name, *args, **kwargs):
            if name == "automation.interop.owner_message":
                raise ImportError("optional envelope unavailable")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", importing)
    else:
        def deliver(*, api, transport_factory, record, thread_name, content, fallback, outcome):
            return fallback(content)

        monkeypatch.setattr(lifecycle, "_origin_notice", lambda: SimpleNamespace(
            deliver=deliver, ThreadOutcome=origin_notice.ThreadOutcome,
        ))
    monkeypatch.setattr(lifecycle, "send_owner_dm", lambda owner, content: sent.append(content))
    # When: the producer builds the terminal notice and routes it with no origin.
    lifecycle._notify_completion({"owner_id": "555"}, "coord-test", "슬롯", "피어 미팅", {"id": "abc123"})
    # Then: compatibility sends precisely today's content, not an envelope or partial text.
    assert sent == [CONTENT]


def test_intermediate_bytes_when_execution_is_unconfirmed(monkeypatch) -> None:
    # Given: a notice with no terminal outcome.
    sent: list[str] = []
    monkeypatch.setattr(lifecycle, "_origin_notice", lambda: origin_notice)
    # When: it is delivered without an execution result.
    lifecycle.notify_result({"id": "abc123"}, "ACK", fallback=sent.append)
    # Then: it must not acquire a successful execution marker.
    assert sent == ["ACK"]


def test_approval_coordinates_when_pending_is_projected() -> None:
    # Given: approval-card coordinates differ from the instruction message.
    entry = pending()
    # When: the pending record is projected for result routing.
    record = entry.origin_record()
    # Then: the envelope receives the saved guild and actual approval card.
    assert record.get("approval_guild_id") == "111"
    assert record.get("dm_message_id") == "333"


@pytest.mark.parametrize("outcome,execution", [
    ("done", "executed"), ("cancelled", "cancelled"), ("expired", "expired"),
])
@pytest.mark.parametrize("guild", ["111", ""])
def test_envelope_when_terminal_result_falls_back(monkeypatch, outcome, execution, guild) -> None:
    # Given: the thread fails; the facade and renderer are real, only transport is injected.
    from automation.interop.owner_message import Action, OwnerMessage, Ref, Result

    record = {**pending().origin_record(), "approval_guild_id": guild}
    sent: list[str] = []
    messages: list[OwnerMessage | None] = []
    real_deliver = origin_notice.deliver

    def deliver(**kwargs):
        messages.append(kwargs.get("message"))
        return real_deliver(**kwargs)

    def unavailable(_channel_id):
        raise OSError("injected transport failure")

    monkeypatch.setattr(lifecycle, "_origin_notice", lambda: origin_notice)
    monkeypatch.setattr(origin_notice, "deliver", deliver)
    monkeypatch.setattr(lifecycle, "_thread_transport", unavailable)
    # When: a result with a known execution state is delivered.
    lifecycle.notify_result(record, CONTENT, fallback=sent.append, outcome=outcome)
    # Then: typed fields preserve producer text, terminal state and saved card coordinates.
    expected = OwnerMessage(
        subject_key="abc123", subject="피어 미팅" if outcome == "done" else "abc123",
        fact=CONTENT, location=Ref(
            scope="message", space="guild" if guild else "unknown", guild_id=guild or None,
            channel_id="222", message_id="333", search=("Discord 검색", "abc123"),
        ), owner=Action(verb="none"), agent_next=None,
        recovery="not_applicable", detail=Result(outcome=execution),
    )
    assert messages == [expected]
    assert len(sent) == 1
    assert len(sent[0].splitlines()) == 5
    assert ("https://discord.com/channels/111/222/333" in sent[0]) == bool(guild)


@pytest.mark.parametrize("thread_fails", [False, True])
def test_cli_result_when_calendar_execution_succeeds(monkeypatch, tmp_path, thread_fails) -> None:
    # Given: a persisted approval and an injected calendar command with a committed effect.
    import coordinate_cli
    entry = pending()
    store = PendingConfirmStore(tmp_path / "pending.jsonl")
    store.append(entry)
    monkeypatch.setenv("COORDINATION_PENDING_CONFIRMS", str(store.path))
    monkeypatch.setenv("CALENDAR_SCRIPTS", str(_REPO / "skills/calendar/scripts"))
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(_REPO / "skills"))
    monkeypatch.setenv("INTEROP_RUNTIME", str(_REPO))
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(lifecycle.io, "interop_config", lambda: {"owner_id": "555"})
    monkeypatch.setattr(lifecycle.io, "run_calendar_cli", lambda argv: SimpleNamespace(
        returncode=0, stdout="EXECUTED event=event-test", stderr="",
    ))
    monkeypatch.setattr(lifecycle, "DiscordApi", lambda owner: SimpleNamespace(
        message_content=lambda saved: f"sha256:{saved.sha256}",
        reaction_users=lambda saved, emoji: (),
    ))
    posts: list[tuple[str, str]] = []

    def post(channel, content):
        posts.append((channel, content))
        return "666"

    def transport(channel):
        if thread_fails:
            raise OSError("injected thread unavailable")
        return SimpleNamespace(send=lambda content: (
            SimpleNamespace(message_id=post(channel, content)),
        ))

    monkeypatch.setattr(lifecycle, "_thread_transport", transport)
    monkeypatch.setattr(lifecycle.io, "team_channel_id", lambda: "777")
    monkeypatch.setattr(lifecycle.io, "owner_approval_channel", lambda owner: "888")
    monkeypatch.setattr(lifecycle.io, "post_message", post)
    monkeypatch.setattr(lifecycle.io, "api", lambda *args: {"name": "coord-test"})
    monkeypatch.setattr(sys, "argv", [
        "coordinate_cli.py", "finalize", "--draft", entry.draft_id,
        "--slot", entry.slot, "--summary", entry.summary, "--correlation", entry.correlation,
    ])
    # When: the real CLI parser, finalize, finish, facade and renderer run.
    code = coordinate_cli.main()
    # Then: both the real owner fallback and the thread surface carry the envelope.
    assert code == 0
    assert posts[-1] == ("888", CLI_FALLBACK_POSTED) if thread_fails else posts[-1] == ("222", CLI_THREAD_POSTED)
    assert posts[0] == ("777", "📅 일정 확정 (coord-test): 2026-07-18 (토) 09:00~09:30 KST — 양측 승인 완료.")
    print(f"CLI exit={code} destination={posts[-1][0]}\n{posts[-1][1]}")


def test_original_bytes_when_renderer_rejects_envelope(monkeypatch) -> None:
    # Given: malformed fields are refused at the optional rendering boundary.
    from automation.interop import owner_message

    def reject(*args, **kwargs):
        raise owner_message.OwnerMessageError(detail="injected")

    sent: list[str] = []
    monkeypatch.setattr(lifecycle, "_origin_notice", lambda: origin_notice)
    monkeypatch.setattr(owner_message, "render", reject)
    # When: a terminal result is sent without an origin.
    lifecycle.notify_result({"id": "abc123"}, CONTENT, fallback=sent.append, outcome="done")
    # Then: the owner still receives the original bytes.
    assert sent == [CONTENT]
