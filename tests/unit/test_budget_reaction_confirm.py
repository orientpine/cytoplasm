"""Budget owner-reaction confirmation, bound to the channel stored on the record.

The surface is resolved once, at post time, by the shared approval policy; every
later read, reaction and delete must take its channel from the draft record. The
resolution ladder itself now lives in ``automation/interop/approval_directory.py``
and is covered by ``tests/unit/test_approval_directory.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "budget" / "scripts"))

import budget_approval  # noqa: E402
import budget_cli  # noqa: E402
import budget_confirm  # noqa: E402
import budget_core  # noqa: E402
import budget_gate  # noqa: E402
from automation.interop.approval_directory import DiscordChannelDirectory  # noqa: E402
from automation.interop.approval_surface import (  # noqa: E402
    ApprovalKind,
    ApprovalSurface,
    reaction_instruction,
)

OWNER_ID = "owner-1"
APPROVALS_CHANNEL = "1528936606856122421"
DM_CHANNEL = "1526487935975952385"
AGENT_CHAT_CHANNEL = "1526487935975952390"
AGENT_CHAT_THREAD = "1526487935975952391"
MESSAGE_ID = "message-1"

type DraftValue = str | int | list[str] | list[list[str]]
type BudgetDraft = dict[str, DraftValue]


@pytest.fixture
def budget_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Confine every budget path to tmp_path and configure one approval channel."""
    config = tmp_path / "interop-config.json"
    config.write_text(
        json.dumps({
            "owner_id": OWNER_ID,
            "personal_approvals_channel_id": APPROVALS_CHANNEL,
            "agent_chat_channel_id": AGENT_CHAT_CHANNEL,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(budget_confirm, "owner_id", lambda: OWNER_ID)


def _draft(*, message_id: str = MESSAGE_ID, bound: bool = True) -> BudgetDraft:
    record: BudgetDraft = {
        "argv": ["gws", "gmail", "+send", "--to", "owner@example.invalid", "--subject", "s", "--body", "b"],
        "changes": [["재료비", "집행액", "0", "50"]],
        "claim_key": "p-to-n",
        "id": "abc123",
        "mail_to": "owner@example.invalid",
        "message_id": message_id,
        "new_hash": "n" * 64,
        "prev_hash": "p" * 64,
        "status": "pending",
        "subject": "s",
    }
    record["sha256"] = budget_core.draft_sha256(record)
    if bound:
        record.update({
            "kind": "budget-mail",
            "surface": "skill-approvals",
            "channel_id": APPROVALS_CHANNEL,
            "policy_version": 1,
        })
    return record


def _channel_facts() -> dict:
    return {"id": APPROVALS_CHANNEL, "type": 0, "name": "approvals"}


def _reaction_api(draft: dict, users_by_emoji: dict[str, list[dict]]):
    """Serve the record's own channel only — any other channel is a routing bug."""

    def request(method: str, path: str, payload: dict | None = None):
        del payload
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return _channel_facts()
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}":
            return {"content": f"draft sha256:{draft['sha256']}"}
        for emoji, users in users_by_emoji.items():
            quoted = budget_confirm.quote(emoji, safe="")
            if method == "GET" and path.endswith(f"/reactions/{quoted}?limit=100"):
                return users
        if method == "GET" and "/reactions/" in path:
            return []
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    return request

def _surface_api(requests: list[tuple[str, str]], draft_sha: str, posted: list[str] | None = None):
    """Serve BOTH approval surfaces, so the channel actually posted to is observable."""

    def request(method: str, path: str, payload: dict | None = None):
        requests.append((method, path))
        if method == "POST" and path == "/users/@me/channels":
            return {"id": DM_CHANNEL}
        if method == "GET" and path == f"/channels/{DM_CHANNEL}":
            return {"type": 1, "name": "", "recipients": [{"id": OWNER_ID}]}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_CHANNEL}":
            return {"type": 0, "name": "agent-chat", "guild_id": "guild-1"}
        if method == "GET" and path == "/guilds/guild-1/threads/active":
            return {"threads": [{
                "id": AGENT_CHAT_THREAD,
                "type": 11,
                "name": "승인-budget-mail",
                "parent_id": AGENT_CHAT_CHANNEL,
            }]}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_THREAD}":
            return {"type": 11, "name": "승인-budget-mail", "parent_id": AGENT_CHAT_CHANNEL}
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return _channel_facts()
        if method == "POST" and path.endswith("/messages"):
            if posted is not None:
                posted.append(str((payload or {}).get("content", "")))
            return {"id": MESSAGE_ID}
        if method == "GET" and path.endswith(f"/messages/{MESSAGE_ID}"):
            return {"content": f"draft sha256:{draft_sha}"}
        if method == "GET":
            return []
        return None

    return request


def _run_watch(
    monkeypatch: pytest.MonkeyPatch, draft: dict, users_by_emoji: dict[str, list[dict]]
) -> tuple[list[budget_gate.Approval], list[str], list[str]]:
    sent: list[budget_gate.Approval] = []
    discarded: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(budget_confirm, "_api", _reaction_api(draft, users_by_emoji))
    monkeypatch.setattr(budget_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(budget_gate, "execute_draft", lambda _draft, approval: sent.append(approval))
    monkeypatch.setattr(budget_gate, "discard_draft", lambda draft_id: discarded.append(draft_id))
    monkeypatch.setattr(budget_confirm, "dm_owner", lambda content: notices.append(content))
    monkeypatch.setattr(budget_cli, "cmd_snapshot", lambda _args: 0)

    assert budget_cli.cmd_watch(argparse.Namespace()) == 0
    return sent, discarded, notices


@pytest.mark.usefixtures("budget_env")
def test_watch_sends_once_when_owner_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a pending draft with an owner-only approval reaction
    draft = _draft()
    # When: the production watch tick resolves the approval message
    sent, discarded, notices = _run_watch(
        monkeypatch, draft, {budget_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the frozen draft is sent exactly once and the owner notice names
    # the mail (subject·recipient·draft id) — uniform result-notice process
    assert len(sent) == 1
    assert discarded == []
    assert len(notices) == 1
    assert "발송 완료" in notices[0]
    assert "owner@example.invalid" in notices[0]
    assert "abc123" in notices[0]


@pytest.mark.usefixtures("budget_env")
def test_watch_discards_when_owner_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a pending draft with an owner-only cancellation reaction
    draft = _draft()
    # When: the production watch tick resolves the approval message
    sent, discarded, notices = _run_watch(
        monkeypatch, draft, {budget_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the draft is discarded, no mail is sent, and the owner notice names
    # the mail (subject·recipient·draft id) instead of a bare fixed phrase
    assert sent == []
    assert discarded == [draft["id"]]
    assert len(notices) == 1
    assert "발송 취소" in notices[0]
    assert "owner@example.invalid" in notices[0]
    assert "abc123" in notices[0]


@pytest.mark.usefixtures("budget_env")
def test_watch_prefers_owner_cancel_when_both_reactions_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the owner has reacted with both outcomes on the same bound message
    draft = _draft()
    # When: the production watch tick resolves reactions
    sent, discarded, notices = _run_watch(
        monkeypatch,
        draft,
        {
            budget_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}],
            budget_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}],
        },
    )
    # Then: cancellation has strict precedence and prevents delivery
    assert sent == []
    assert discarded == [draft["id"]]
    assert len(notices) == 1
    assert "발송 취소" in notices[0]


@pytest.mark.usefixtures("budget_env")
@pytest.mark.parametrize("user", [{"id": OWNER_ID, "bot": True}, {"id": "other", "bot": False}])
def test_watch_ignores_bot_or_non_owner_reactions(
    monkeypatch: pytest.MonkeyPatch, user: dict
) -> None:
    # Given: the only approval reaction is from a bot or someone other than the owner
    draft = _draft()
    # When: the production watch tick resolves reactions
    sent, discarded, notices = _run_watch(
        monkeypatch, draft, {budget_confirm.APPROVE_EMOJI: [user]}
    )
    # Then: the draft remains pending with no external effect
    assert sent == [] and discarded == [] and notices == []


@pytest.mark.usefixtures("budget_env")
def test_reaction_resolver_rejects_message_without_draft_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the approval message references no hash for this frozen draft
    draft = _draft()

    def request(method: str, path: str, payload: dict | None = None):
        del payload
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return _channel_facts()
        return {"content": "unrelated approval message"}

    monkeypatch.setattr(budget_confirm, "_api", request)
    # When / Then: the resolver fails closed before accepting the reaction
    with pytest.raises(budget_gate.GateError, match="해시"):
        budget_confirm.resolve_reaction(draft)


@pytest.mark.usefixtures("budget_env")
def test_watch_preadds_approve_then_cancel_reactions(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a pending draft that has not yet been posted for approval
    draft = _draft(message_id="", bound=False)
    requests: list[tuple[str, str]] = []
    monkeypatch.setattr(
        budget_gate,
        "set_message_id",
        lambda item, message_id, _binding=None: {**item, "message_id": message_id},
    )
    monkeypatch.setattr(budget_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(budget_cli, "cmd_snapshot", lambda _args: 0)
    monkeypatch.setattr(budget_confirm, "_api", _surface_api(requests, str(draft["sha256"])))
    # When: the watch tick posts a missing approval request
    assert budget_cli.cmd_watch(argparse.Namespace()) == 0
    # Then: the bot pre-adds the exact reactions in approve, then cancel order
    assert [path for method, path in requests if method == "PUT"] == [
        f"/channels/{AGENT_CHAT_THREAD}/messages/{MESSAGE_ID}/reactions/%E2%9C%85/@me",
        f"/channels/{AGENT_CHAT_THREAD}/messages/{MESSAGE_ID}/reactions/%E2%9B%94/@me",
    ]


def _poison_every_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any fresh surface resolution explode, so only the record can answer."""

    def refuse(*_args: object, **_kwargs: object) -> str:
        raise budget_gate.GateError("승인 표면을 다시 해석해서는 안 됩니다", 3)

    monkeypatch.setattr(budget_confirm, "approvals_channel_id", refuse, raising=False)
    monkeypatch.setattr(DiscordChannelDirectory, "skill_approvals", refuse, raising=False)
    monkeypatch.setattr(DiscordChannelDirectory, "owner_dm", refuse, raising=False)


@pytest.mark.usefixtures("budget_env")
def test_confirm_reads_the_channel_from_the_record_not_the_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a posted draft whose record stores its bound channel, a Discord fake
    # that answers for that channel only, and every resolver poisoned to raise
    draft = _draft()
    monkeypatch.setattr(
        budget_confirm,
        "_api",
        _reaction_api(draft, {budget_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}),
    )
    _poison_every_resolver(monkeypatch)
    # When: the production reaction resolver reads the owner's decision
    action = budget_confirm.resolve_reaction(draft)
    # Then: nothing was re-resolved and the owner's ✅ came from the bound channel
    assert action == budget_confirm.APPROVE_EMOJI


@pytest.mark.usefixtures("budget_env")
def test_record_predating_the_binding_schema_still_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a posted draft written before the binding columns existed
    draft = _draft(bound=False)
    monkeypatch.setattr(
        budget_confirm,
        "_api",
        _reaction_api(draft, {budget_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}),
    )
    # When: the production reaction resolver reads the owner's decision
    action = budget_confirm.resolve_reaction(draft)
    # Then: the legacy migrator resolves the surface and the record stays consumable
    assert action == budget_confirm.APPROVE_EMOJI


@pytest.mark.usefixtures("budget_env")
def test_a_record_bound_to_a_foreign_surface_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a record whose stored surface contradicts the policy it was stamped under
    draft = _draft()
    draft["surface"] = "owner-dm"
    monkeypatch.setattr(budget_confirm, "_api", _reaction_api(draft, {}))
    # When / Then: the contradiction is refused instead of silently retargeted
    with pytest.raises(budget_gate.GateError, match="검증 실패"):
        budget_confirm.resolve_reaction(draft)


@pytest.mark.usefixtures("budget_env")
def test_request_posts_to_the_agent_chat_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a never-posted budget draft and a Discord fake that answers for both surfaces
    draft = _draft(message_id="", bound=False)
    requests: list[tuple[str, str]] = []
    posted: list[str] = []
    monkeypatch.setattr(
        budget_confirm, "_api", _surface_api(requests, str(draft["sha256"]), posted)
    )

    # When: the lifecycle posts the one hash-bound approval request for this draft
    message_id = budget_approval.post_for_approval(draft)

    # Then: the single approval message lands in its agent-chat thread, never the guild channel,
    # and its reaction line is the one the policy renders for that surface
    assert message_id == MESSAGE_ID
    posts = [path for method, path in requests if method == "POST" and path.endswith("/messages")]
    assert posts == [f"/channels/{AGENT_CHAT_THREAD}/messages"]
    assert reaction_instruction(
        ApprovalKind.BUDGET_MAIL, ApprovalSurface.AGENT_CHAT_THREAD
    ) in posted[0]


def test_outbound_mail_body_names_no_channel() -> None:
    # Given: one detected ledger change destined for the institution's request mail
    changes = [budget_core.Change("재료비", "집행액", "0", "50")]

    # When: the outbound mail is rendered for that third-party recipient
    _subject, body = budget_core.render_mail(
        changes, prev_hash="p" * 64, new_hash="n" * 64, now=datetime(2026, 7, 27, tzinfo=UTC)
    )

    # Then: it still asserts the owner approved, but names no internal approval surface
    assert "#approvals" not in body
    assert "소유자의 명시적 승인(✅) 이후에만 발송되었습니다" in body


# ------------------------------------------------- origin-thread result routing

ORIGIN_CHANNEL = "200000000000000001"
ORIGIN_MESSAGE = "origin-message-1"
THREAD_ID = "300000000000000001"


def _origin_draft() -> BudgetDraft:
    return {**_draft(), "origin_channel_id": ORIGIN_CHANNEL, "origin_message_id": ORIGIN_MESSAGE}


class _SentChunk:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


def _run_origin_watch(
    monkeypatch: pytest.MonkeyPatch,
    draft: dict,
    users_by_emoji: dict[str, list[dict]],
    *,
    thread_fail: bool = False,
):
    sent: list[budget_gate.Approval] = []
    discarded: list[str] = []
    dm_notices: list[str] = []
    thread_calls: list[dict | None] = []
    thread_posts: list[tuple[str, str]] = []
    base = _reaction_api(draft, users_by_emoji)

    def api(method: str, path: str, payload: dict | None = None):
        if method == "POST" and path == (
            f"/channels/{ORIGIN_CHANNEL}/messages/{ORIGIN_MESSAGE}/threads"
        ):
            if thread_fail:
                raise RuntimeError("thread API down")
            thread_calls.append(payload)
            return {"id": THREAD_ID}
        return base(method, path, payload)

    class _ThreadTransport:
        def __init__(self, channel_id: str) -> None:
            self.channel_id = channel_id

        def send(self, content: str) -> tuple[_SentChunk, ...]:
            thread_posts.append((self.channel_id, content))
            return (_SentChunk("thread-post-1"),)

    monkeypatch.setattr(budget_confirm, "_api", api)
    monkeypatch.setattr(budget_confirm, "_thread_transport", _ThreadTransport)
    monkeypatch.setattr(budget_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(budget_gate, "execute_draft", lambda _d, approval: sent.append(approval))
    monkeypatch.setattr(budget_gate, "discard_draft", lambda draft_id: discarded.append(draft_id))
    monkeypatch.setattr(budget_confirm, "dm_owner", lambda content: dm_notices.append(content))
    monkeypatch.setattr(budget_cli, "cmd_snapshot", lambda _args: 0)
    assert budget_cli.cmd_watch(argparse.Namespace()) == 0
    return sent, discarded, dm_notices, thread_calls, thread_posts


@pytest.mark.usefixtures("budget_env")
def test_watch_posts_send_result_to_origin_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an origin-bound draft with an owner-only ✅
    draft = _origin_draft()
    # When: the production watch tick resolves the approval
    sent, discarded, dm_notices, thread_calls, thread_posts = _run_origin_watch(
        monkeypatch, draft, {budget_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the result lands in the origin-channel thread, never the owner DM
    assert len(sent) == 1 and discarded == []
    assert dm_notices == []
    assert len(thread_calls) == 1
    assert [channel for channel, _content in thread_posts] == [THREAD_ID]
    content = thread_posts[0][1]
    assert "발송 완료" in content
    assert "owner@example.invalid" in content
    assert "abc123" in content


@pytest.mark.usefixtures("budget_env")
def test_watch_posts_cancel_result_to_origin_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an origin-bound draft the owner cancels with ⛔
    draft = _origin_draft()
    # When: the production watch tick resolves the cancellation
    sent, discarded, dm_notices, _thread_calls, thread_posts = _run_origin_watch(
        monkeypatch, draft, {budget_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the discard result lands in the origin thread with full mail context
    assert sent == []
    assert discarded == [draft["id"]]
    assert dm_notices == []
    assert [channel for channel, _content in thread_posts] == [THREAD_ID]
    content = thread_posts[0][1]
    assert "발송 취소" in content
    assert "owner@example.invalid" in content
    assert "abc123" in content


@pytest.mark.usefixtures("budget_env")
def test_watch_falls_back_to_dm_when_thread_creation_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an origin-bound approved draft whose thread creation will fail
    draft = _origin_draft()
    # When: the watch tick sends and tries to report to the origin thread
    sent, _discarded, dm_notices, _thread_calls, thread_posts = _run_origin_watch(
        monkeypatch, draft,
        {budget_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]},
        thread_fail=True,
    )
    # Then: the committed send survives and the result falls back to the owner DM
    assert len(sent) == 1
    assert thread_posts == []
    assert len(dm_notices) == 1 and "발송 완료" in dm_notices[0]
    assert "NOTIFY-THREAD-FAIL" in capsys.readouterr().err


def test_create_draft_persists_origin_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a channel-initiated snapshot instruction carrying its origin refs
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    # When: the draft is created with the origin binding
    draft = budget_gate.create_draft(
        changes=[budget_core.Change("재료비", "집행액", "0", "50")],
        subject="s", body="b", recipient="self@example.invalid",
        prev_hash="p" * 64, new_hash="n" * 64, claim_key="k",
        origin_channel_id=ORIGIN_CHANNEL, origin_message_id=ORIGIN_MESSAGE,
    )
    # Then: the stored record carries the origin binding for result routing
    stored = json.loads(
        (tmp_path / "gate" / "drafts" / f"{draft['id']}.json").read_text(encoding="utf-8")
    )
    assert stored["origin_channel_id"] == ORIGIN_CHANNEL
    assert stored["origin_message_id"] == ORIGIN_MESSAGE


def test_snapshot_threads_origin_into_the_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a baseline snapshot followed by a sheet change, instructed from a channel
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("BUDGET_DB", str(tmp_path / "budget.db"))
    header = [[], [], [], [], [], list(budget_core.HEADER_EXPECTED)]
    feed = iter([
        [*header, ["인건비", "100", "10", "90", "d"]],
        [*header, ["인건비", "100", "20", "80", "d"]],
    ])
    monkeypatch.setattr(budget_cli, "read_balance_values", lambda _sheet_id="": next(feed))
    monkeypatch.setattr(budget_gate, "mail_to", lambda: "self@example.invalid")
    assert budget_cli._snapshot(post=False) == 0
    # When: the change snapshot runs with the origin binding
    assert budget_cli._snapshot(
        post=False, origin_channel_id=ORIGIN_CHANNEL, origin_message_id=ORIGIN_MESSAGE
    ) == 0
    # Then: the created draft carries the origin binding
    [draft_path] = (tmp_path / "gate" / "drafts").glob("*.json")
    stored = json.loads(draft_path.read_text(encoding="utf-8"))
    assert stored["origin_channel_id"] == ORIGIN_CHANNEL
    assert stored["origin_message_id"] == ORIGIN_MESSAGE


def test_snapshot_parser_accepts_origin_flags() -> None:
    # Given/When: the snapshot subcommand is parsed with origin flags
    args = budget_cli.build_parser().parse_args([
        "snapshot", "--no-post",
        "--origin-channel-id", ORIGIN_CHANNEL, "--origin-message-id", ORIGIN_MESSAGE,
    ])
    # Then: the namespace carries them for _snapshot
    assert args.origin_channel_id == ORIGIN_CHANNEL
    assert args.origin_message_id == ORIGIN_MESSAGE


def test_notify_result_falls_back_to_owner_when_helper_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the interop runtime lacks origin_notice (stale runtime / sandbox)
    notices: list[str] = []
    monkeypatch.setattr(budget_confirm, "dm_owner", lambda content: notices.append(content) or "dm-1")

    def missing():
        raise ImportError("No module named 'automation'")

    monkeypatch.setattr(budget_confirm, "_origin_notice", missing)
    # When: an origin-bound result is delivered
    result = budget_confirm.notify_result(_origin_draft(), "결과")
    # Then: the owner still gets it through the legacy path, with a marker
    assert result == "dm-1" and notices == ["결과"]
    assert "NOTIFY-HELPER-MISSING" in capsys.readouterr().err
