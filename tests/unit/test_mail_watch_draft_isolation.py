"""Watch tick 의 초안 단위 격리 특성 고정 (repair t_0c46c0ad 2부).

옛 pending 초안의 승인 메시지가 사라져 404 가 나도 그 실패는 그 초안에만 머물러야 하고,
죽은 바인딩은 승인 라이프사이클의 cleanup 이 풀어 다시 게시해야 한다. 두 동작 모두 이미
올바르므로 여기서는 회귀만 고정한다(프로덕션 코드 변경 없음).
"""
from __future__ import annotations

import argparse
import json
import sys
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

import triage_approval  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
from test_mail_single_live_request import (  # noqa: E402
    AGENT_CHAT_CHANNEL,
    OWNER,
    FakeDiscord,
)

APPROVALS_CHANNEL = "100000000000000001"
STALE_MESSAGE = "message-stale"
LIVE_MESSAGE = "message-live"


def _draft(draft_id: str, uid: str, message_id: str) -> dict:
    record = {
        "argv": ["python3", "-m", "mailon.main", "send", "--to", "owner@example.com"],
        "body": "회신 본문",
        "category": "important",
        "channel_id": APPROVALS_CHANNEL,
        "flags": ["reply_needed"],
        "id": draft_id,
        "kind": "reply",
        "mail_subject": "문의",
        "message_id": message_id,
        "policy_version": 1,
        "sender": "발신자 <sender@example.com>",
        "sender_masked": triage_core.mask_value("발신자 <sender@example.com>"),
        "sensitive": False,
        "status": "pending",
        "subject": "Re: 문의",
        "surface": "skill-approvals",
        "tags": [],
        "to": "owner@example.com",
        "uid": uid,
        "uid_opaque": triage_core.mask_value(uid),
    }
    record["sha256"] = triage_core.draft_sha256(record)
    return record


def _api_with_one_missing_message(drafts: tuple[dict, ...], missing: str):
    """Discord fake where exactly one approval message has been deleted (404)."""
    approve = quote(triage_confirm.APPROVE_EMOJI, safe="")
    by_message = {str(draft["message_id"]): draft for draft in drafts}

    def request(method: str, path: str, payload: dict | None = None):
        del payload
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return {"type": 0, "name": "approvals", "recipients": []}
        for message_id, draft in by_message.items():
            if method != "GET" or path != f"/channels/{APPROVALS_CHANNEL}/messages/{message_id}":
                continue
            if message_id == missing:
                raise HTTPError(
                    f"https://discord.invalid/channels/{APPROVALS_CHANNEL}/messages/{message_id}",
                    404,
                    "Not Found",
                    Message(),
                    None,
                )
            return {"id": message_id, "content": f"draft sha256:{draft['sha256']}"}
        if method == "GET" and path.endswith(f"/reactions/{approve}?limit=100"):
            return [{"id": OWNER, "bot": False}]
        if method == "GET" and "/reactions/" in path:
            return []
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    return request


def _mail_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeDiscord, Path]:
    """Confine the draft store and the Discord surface to tmp_path."""
    fake = FakeDiscord([])
    interop = tmp_path / "interop-config.json"
    interop.write_text(
        json.dumps({"owner_id": OWNER, "agent_chat_channel_id": AGENT_CHAT_CHANNEL}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "mail-gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail-home"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER)
    monkeypatch.setattr(triage_confirm, "_api", fake.api)
    return fake, tmp_path / "mail-gate" / "drafts"


def test_watch_when_an_old_pending_draft_is_404_then_the_next_draft_still_sends(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: two pending drafts whose first approval message no longer exists
    stale = _draft("stale1", "u-stale", STALE_MESSAGE)
    live = _draft("live12", "u-live", LIVE_MESSAGE)
    sent: list[tuple[str, str]] = []
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER)
    monkeypatch.setattr(
        triage_confirm, "_api", _api_with_one_missing_message((stale, live), STALE_MESSAGE)
    )
    monkeypatch.setattr(triage_confirm, "notify_result", lambda _draft, _content: "notified")
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [stale, live])
    monkeypatch.setattr(
        triage_gate,
        "execute_draft",
        lambda draft, approval: sent.append((str(draft["id"]), approval.ref)),
    )
    monkeypatch.setattr(
        triage_gate,
        "discard_draft",
        lambda draft_id: pytest.fail(f"a 404 must not discard draft {draft_id}"),
    )

    # When: the production watch tick walks both drafts
    rc = triage_cli.cmd_watch(argparse.Namespace())

    # Then: the 404 stays inside its own draft and the approved sibling is still sent
    captured = capsys.readouterr()
    assert rc == 0
    assert sent == [("live12", f"reaction:{LIVE_MESSAGE}")]
    assert f"REACTION-RETRY draft={stale['id']}" in captured.err
    assert f"SENT draft={live['id']} method=manual_reaction" in captured.out


def test_when_an_old_approval_message_is_gone_then_the_lifecycle_unbinds_and_reposts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a pending draft bound to an approval message that has since been deleted
    fake, drafts = _mail_env(tmp_path, monkeypatch)
    record = triage_gate.create_draft(
        uid="u-1", sender="발신자 <sender@example.com>", mail_subject="문의",
        to="owner@example.com", subject="Re: 문의", body="회신 본문",
        sensitive=False, tags=(), category="important", flags=("reply_needed",),
    )
    assert triage_approval.post_for_approval(record) == "m-1"
    fake.contents.pop("m-1")

    # When: the approval producer runs again for that same draft
    verdict = triage_approval.request_approval(record)

    # Then: the dead binding is cleared as missing and exactly one new message replaces it
    lifecycle = triage_approval.lifecycle()
    stored = json.loads((drafts / f"{record['id']}.json").read_text(encoding="utf-8"))
    assert verdict.outcome is lifecycle.Outcome.POSTED
    assert [item.reason for item in verdict.cleared] == [lifecycle.Reason.MESSAGE_MISSING]
    assert stored["message_id"] == "m-2"
    assert stored["status"] == "pending"
    assert fake.posts == 2
    assert "DELETE:m-1" not in fake.calls
