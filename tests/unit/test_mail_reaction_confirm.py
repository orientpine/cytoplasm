from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

from automation.interop.approval_surface import (  # noqa: E402
    ApprovalKind,
    ApprovalSurface,
    required_surface,
)
from automation.interop.discord_transport import (  # noqa: E402
    DiscordTransport,
    SentMessage,
)
import triage_cli  # noqa: E402
import triage_approval  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
import triage_pipeline  # noqa: E402


OWNER_ID = "owner-1"
APPROVALS_CHANNEL = "100000000000000001"
DM_CHANNEL = "100000000000000002"
MESSAGE_ID = "message-1"


def _draft(*, message_id: str = MESSAGE_ID) -> dict:
    record = {
        "argv": ["python3", "-m", "mailon.main", "send", "--to", "owner@example.invalid"],
        "body": "안녕하세요.",
        "category": "important",
        "flags": ["reply_needed"],
        "channel_id": APPROVALS_CHANNEL,
        "id": "abc123",
        "kind": "reply",
        "mail_subject": "일정 문의",
        "message_id": message_id,
        "sender": "발신자 <sender@example.invalid>",
        "sender_masked": triage_core.mask_value("발신자 <sender@example.invalid>"),
        "sensitive": False,
        "surface": "skill-approvals",
        "status": "pending",
        "subject": "Re: 일정 문의",
        "tags": [],
        "to": "owner@example.invalid",
        "uid": "u-1",
        "uid_opaque": triage_core.mask_value("u-1"),
        "policy_version": 1,
    }
    record["sha256"] = triage_core.draft_sha256(record)
    return record


def _reaction_api(draft: dict, users_by_emoji: dict[str, list[dict]]):
    def request(method: str, path: str, payload: dict | None = None):
        del payload
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return {"type": 0, "name": "approvals", "recipients": []}
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}":
            return {"content": f"draft sha256:{draft['sha256']}"}
        for emoji, users in users_by_emoji.items():
            quoted = triage_confirm.quote(emoji, safe="")
            if method == "GET" and path.endswith(f"/reactions/{quoted}?limit=100"):
                return users
        if method == "GET" and "/reactions/" in path:
            return []
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    return request


def _run_watch(monkeypatch: pytest.MonkeyPatch, draft: dict, users_by_emoji: dict[str, list[dict]]):
    sent: list[triage_gate.Approval] = []
    discarded: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", _reaction_api(draft, users_by_emoji))
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(triage_gate, "execute_draft", lambda _draft, approval: sent.append(approval))
    monkeypatch.setattr(triage_gate, "discard_draft", lambda draft_id: discarded.append(draft_id))
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda content: notices.append(content))
    monkeypatch.setattr(triage_cli, "cmd_process", lambda _args: 0)

    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    return sent, discarded, notices


def test_watch_sends_once_when_owner_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a pending draft with an owner-only approval reaction
    draft = _draft()
    # When: the production watch tick resolves the approval message
    sent, discarded, notices = _run_watch(
        monkeypatch, draft, {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the frozen draft is sent exactly once, discard untouched, and the
    # owner receives a send-success DM (owner request 2026-07-20)
    assert len(sent) == 1
    assert discarded == []
    assert len(notices) == 1
    assert "발송 완료" in notices[0]
    assert "Re: 일정 문의" in notices[0]
    assert "owner@example.invalid" in notices[0]
    assert "abc123" in notices[0]


def test_watch_discards_when_owner_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a pending draft with an owner-only cancellation reaction
    draft = _draft()
    # When: the production watch tick resolves the approval message
    sent, discarded, notices = _run_watch(
        monkeypatch, draft, {triage_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the draft is discarded, no mail is sent, and the owner is notified
    assert sent == []
    assert discarded == [draft["id"]]
    assert notices == ["메일 발송 취소됨"]


def test_new_reply_draft_posts_to_the_owner_dm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Given: a sensitive reply draft and a Discord fake that can describe both surfaces
    requests: list[tuple[str, str, dict | None]] = []
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)

    def request(method: str, path: str, payload: dict | None = None):
        requests.append((method, path, payload))
        if method == "POST" and path == "/users/@me/channels":
            return {"id": DM_CHANNEL}
        if method == "GET" and path == f"/channels/{DM_CHANNEL}":
            return {"type": 1, "name": "", "recipients": [{"id": OWNER_ID}]}
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return {"type": 0, "name": "approvals", "recipients": []}
        if method == "POST" and path.endswith("/messages"):
            return {"id": MESSAGE_ID}
        if method == "PUT":
            return None
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    monkeypatch.setattr(triage_confirm, "_api", request)
    monkeypatch.setattr(
        triage_pipeline.triage_llm,
        "draft_reply",
        lambda **_kwargs: ("Re: 민감 문의", "민감 회신 전문", "test-provider"),
    )
    monkeypatch.setattr(
        triage_confirm,
        "dm_owner",
        lambda _content: pytest.fail("sensitive reply must not post a second owner-DM message"),
    )

    # When: the reply pipeline drafts and posts the sensitive approval
    actions = triage_pipeline._draft_and_post(
        {
            "uid": "u-dm",
            "sender": "발신자 <sender@example.invalid>",
            "subject": "민감 문의",
            "body": "원본 민감 메일 본문",
        },
        SimpleNamespace(sensitive=True, tags=("patent-sensitive",)),
        SimpleNamespace(category="important", flags=lambda: ("reply_needed",)),
        post=True,
    )

    # Then: the one approval message is the owner-DM message with full reply and hash
    [draft] = triage_gate.list_drafts()
    posts = [item for item in requests if item[0] == "POST" and item[1].endswith("/messages")]
    assert actions == [f"draft:{draft['id']}", f"posted:{MESSAGE_ID}"]
    assert len(posts) == 1
    _method, channel_path, payload = posts[0]
    assert channel_path == f"/channels/{DM_CHANNEL}/messages"
    assert payload is not None
    assert "민감 회신 전문" in str(payload["content"])
    assert draft["sha256"] in str(payload["content"])
    assert [item[:2] for item in requests if item[0] == "PUT"] == [
        ("PUT", f"/channels/{DM_CHANNEL}/messages/{MESSAGE_ID}/reactions/%E2%9C%85/@me"),
        ("PUT", f"/channels/{DM_CHANNEL}/messages/{MESSAGE_ID}/reactions/%E2%9B%94/@me"),
    ]


def test_v1_bound_reply_pending_is_still_consumable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a reply record stored with the R1 guild binding and an owner ✅ there
    draft = _draft()
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        triage_confirm,
        "_api",
        _reaction_api(draft, {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}),
    )

    # When: current policy has moved new replies to the owner DM, but the stored binding is read
    binding = triage_approval.stored_binding(draft)
    action = triage_confirm.resolve_reaction(draft)

    # Then: the stored v1 guild message remains valid and consumable without retargeting
    assert required_surface(ApprovalKind.MAIL_REPLY) is ApprovalSurface.OWNER_DM
    assert binding.channel_id == APPROVALS_CHANNEL
    assert binding.surface is ApprovalSurface.SKILL_APPROVALS
    assert action == triage_confirm.APPROVE_EMOJI


def test_supersede_deletes_from_the_bound_channel_not_the_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a superseded approval message that was posted in the owner's DM
    dm_channel = "100000000000000002"
    calls: list[tuple[str, str]] = []

    def record(method: str, path: str, payload: dict | None = None):
        del payload
        calls.append((method, path))
        return None

    monkeypatch.setattr(triage_confirm, "_api", record)
    # When: the superseded message is deleted
    draft = {**_draft(), "channel_id": dm_channel, "surface": "owner-dm"}
    request = triage_approval.lifecycle().ApprovalRequest(
        key="mail:reply:u-1",
        action_hash=draft["sha256"],
        message_id=MESSAGE_ID,
        channel_id=dm_channel,
        created_at="2026-07-26T00:00:00Z",
    )
    triage_approval.MailApprovalGate(draft).delete(request)
    # Then: deletion uses the channel where the message was actually posted
    assert ("DELETE", f"/channels/{dm_channel}/messages/{MESSAGE_ID}") in calls
    assert ("DELETE", f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}") not in calls


def test_pending_drafts_accepts_legacy_records_with_missing_kind_and_channel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: seven production-shaped legacy records beside a current pending draft
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    drafts = tmp_path / "gate" / "drafts"
    drafts.mkdir(parents=True)
    current = _draft(message_id="")
    (drafts / "current.json").write_text(json.dumps(current), encoding="utf-8")
    for index in range(7):
        legacy = {**_draft(message_id=""), "id": f"legacy{index}", "kind": None, "channel_id": None}
        (drafts / f"legacy{index}.json").write_text(json.dumps(legacy), encoding="utf-8")
    # When: the lifecycle reads every pending draft
    pending = triage_approval._pending_drafts()
    # Then: legacy records are consumable and do not reject the whole store
    assert len(pending) == 8
    assert {key for _path, _record, key in pending} == {"mail:reply:u-1"}


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
            triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}],
            triage_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}],
        },
    )
    # Then: cancellation has strict precedence and prevents delivery
    assert sent == []
    assert discarded == [draft["id"]]
    assert notices == ["메일 발송 취소됨"]


@pytest.mark.parametrize("user", [{"id": OWNER_ID, "bot": True}, {"id": "other", "bot": False}])
def test_watch_ignores_bot_or_non_owner_reactions(
    monkeypatch: pytest.MonkeyPatch, user: dict
) -> None:
    # Given: the only approval reaction is from a bot or someone other than the owner
    draft = _draft()
    # When: the production watch tick resolves reactions
    sent, discarded, notices = _run_watch(
        monkeypatch, draft, {triage_confirm.APPROVE_EMOJI: [user]}
    )
    # Then: the draft remains pending with no external effect
    assert sent == [] and discarded == [] and notices == []


def test_reaction_resolver_rejects_message_without_draft_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the approval message references no hash for this frozen draft
    draft = _draft()
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        triage_confirm,
        "_api",
        lambda method, path, _payload=None: {"type": 0, "name": "approvals", "recipients": []}
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}"
        else {"content": "unrelated approval message"},
    )
    # When / Then: the resolver fails closed before accepting the reaction
    with pytest.raises(triage_gate.GateError, match="해시"):
        triage_confirm.resolve_reaction(draft)


def test_watch_preadds_approve_then_cancel_reactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a pending draft that has not yet been posted to #approvals
    draft = _draft(message_id="")
    requests: list[tuple[str, str]] = []
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(
        triage_gate,
        "set_message_id",
        lambda item, message_id, _channel_id: {**item, "message_id": message_id},
    )
    monkeypatch.setattr(triage_gate, "set_approval_binding", lambda item, **_binding: item)
    monkeypatch.setattr(triage_cli, "cmd_process", lambda _args: 0)

    def request(method: str, path: str, payload: dict | None = None):
        del payload
        requests.append((method, path))
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return {"type": 0, "name": "approvals", "recipients": []}
        if method == "POST":
            return {"id": MESSAGE_ID}
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}":
            return {"content": f"draft sha256:{draft['sha256']}"}
        if method == "GET":
            return []
        return None

    monkeypatch.setattr(triage_confirm, "_api", request)
    # When: the watch tick posts a missing approval request
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    # Then: the bot pre-adds the exact reactions in approve, then cancel order
    assert [item for item in requests if item[0] == "PUT"] == [
        ("PUT", f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}/reactions/%E2%9C%85/@me"),
        ("PUT", f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}/reactions/%E2%9B%94/@me"),
    ]


def test_watch_survives_transient_reaction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the reaction lookup hits a transient Discord failure (HTTP 429)
    draft = _draft()
    sent: list[triage_gate.Approval] = []
    discarded: list[str] = []

    def rate_limited(method: str, path: str, payload: dict | None = None):
        del method, payload
        if path == f"/channels/{APPROVALS_CHANNEL}":
            return {"type": 0, "name": "approvals", "recipients": []}
        raise triage_confirm.HTTPError("https://discord.test", 429, "Too Many Requests", None, None)

    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", rate_limited)
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(triage_gate, "execute_draft", lambda _draft, approval: sent.append(approval))
    monkeypatch.setattr(triage_gate, "discard_draft", lambda draft_id: discarded.append(draft_id))
    monkeypatch.setattr(triage_cli, "cmd_process", lambda _args: 0)

    # When: the watch tick runs into the rate limit for this draft
    rc = triage_cli.cmd_watch(argparse.Namespace())

    # Then: the tick survives (rc=0), the draft stays pending for the next tick
    assert rc == 0
    assert sent == [] and discarded == []


def test_watch_send_notification_failure_never_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an approved draft whose success-notification DM will fail
    draft = _draft()
    sent: list[triage_gate.Approval] = []
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        triage_confirm, "_api",
        _reaction_api(draft, {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}),
    )
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(triage_gate, "execute_draft", lambda _d, approval: sent.append(approval))

    def _boom(_content: str) -> str:
        raise RuntimeError("discord down")

    monkeypatch.setattr(triage_confirm, "dm_owner", _boom)
    # When/Then: the tick still succeeds and the committed send is not undone
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    assert len(sent) == 1


def test_dm_owner_chunks_long_content(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an owner DM whose Korean and ASCII content exceeds one Discord message
    original = ("민감한 초안 review-123 " * 300)[:4500]
    chunks: list[str] = []
    transport = DiscordTransport(token="dummy-token", channel_id="chan-1")

    def send_chunk(_transport: DiscordTransport, chunk: str) -> SentMessage:
        chunks.append(chunk)
        return SentMessage(message_id=str(len(chunks)))

    monkeypatch.setattr(DiscordTransport, "_send_chunk", send_chunk)
    monkeypatch.setattr(triage_confirm, "_dm_transport", lambda _channel_id: transport)
    monkeypatch.setattr(
        triage_approval,
        "approval_directory",
        lambda: SimpleNamespace(owner_dm=lambda: "chan-1"),
    )

    # When: the owner DM is sent through the shared transport
    message_id = triage_confirm.dm_owner(original)

    # Then: three ordered, bounded chunks are sent and the last ID is returned
    assert len(chunks) == 3
    assert all(len(chunk) <= 2000 for chunk in chunks)
    assert chunks == [original[:2000], original[2000:4000], original[4000:]]
    assert "".join(chunks) == original
    assert message_id == "3"


def test_dm_owner_single_send_short_content(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an owner DM within Discord's message limit
    original = "짧은 owner DM short-내용"
    chunks: list[str] = []
    transport = DiscordTransport(token="dummy-token", channel_id="chan-1")

    def send_chunk(_transport: DiscordTransport, chunk: str) -> SentMessage:
        chunks.append(chunk)
        return SentMessage(message_id=str(len(chunks)))

    monkeypatch.setattr(DiscordTransport, "_send_chunk", send_chunk)
    monkeypatch.setattr(triage_confirm, "_dm_transport", lambda _channel_id: transport)
    monkeypatch.setattr(
        triage_approval,
        "approval_directory",
        lambda: SimpleNamespace(owner_dm=lambda: "chan-1"),
    )

    # When: the short owner DM is sent through the shared transport
    message_id = triage_confirm.dm_owner(original)

    # Then: one unchanged chunk is sent and its ID is returned
    assert chunks == [original]
    assert message_id == "1"
