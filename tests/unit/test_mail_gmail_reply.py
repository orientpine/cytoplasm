"""Gmail reply card: `compose --reply-gmail <id>` through the existing Gmail approval path.

Owner 2026-11-02 "지메일로 써": YooDori had to answer a Gmail message from the Gmail
account, and compose only built MailOn drafts (`--to` required, no Gmail producer), so
the Gmail `+reply` action that `mail_gmail_send` and the Gmail gate already knew had no
way onto a card. These tests drive the real CLI with a fake gws read and the real
watcher execution with a fake gws send.
"""
from __future__ import annotations

import functools
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

import mail_card_compact  # noqa: E402
import mail_forward  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
from test_gmail_approval_gate import OWNER_ID  # noqa: E402
from test_gmail_send_e2e import _install_fake_gws  # noqa: E402
from test_mail_card_v4_supersede import owner_thread as owner_thread  # noqa: E402
from test_mail_single_live_request import mail_env as mail_env  # noqa: E402

MESSAGE = "1a19c50944a8fa9b"
BODY = "확인했습니다. 다음 주에 반영하겠습니다."
CC = "colleague@example.invalid"
HEADERS = [{"name": "From", "value": "Notifier <notify@example.invalid>"},
           {"name": "Reply-To", "value": "reply+thread@example.invalid"},
           {"name": "Subject", "value": "[repo] Issue #1814 opened"}]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    mode_file = tmp_path / "runtime" / "mail-mode.json"
    mode_file.parent.mkdir(parents=True)
    mode_file.write_text(json.dumps({"mode": "full-go", "source": "test"}), encoding="utf-8")
    for name, value in {
        "TRIAGE_GATE_DIR": tmp_path / "gate", "TRIAGE_DB": tmp_path / "triage.db",
        "TRIAGE_MAIL_HOME": tmp_path / "mail", "TRIAGE_MAIL_MODE_FILE": mode_file,
        "TRIAGE_MAIL_MODE_REPO": tmp_path / "absent-repo-mode.json",
        "TRIAGE_APPROVAL_LOG": tmp_path / "approvals.jsonl",
    }.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    return tmp_path


def _reader(monkeypatch: pytest.MonkeyPatch, *, rc: int = 0, headers: list | None = None) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        message = {"id": MESSAGE, "payload": {"headers": HEADERS if headers is None else headers}}
        return subprocess.CompletedProcess(argv, rc, json.dumps(message), "")

    monkeypatch.setattr(mail_forward, "gmail_reply_target",
                        functools.partial(mail_forward.gmail_reply_target, runner=run))
    return calls


def _cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["triage_cli", *argv])
    return triage_cli.main()


def _drafts() -> list[dict]:
    directory = triage_gate._public_drafts_dir()
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))] \
        if directory.is_dir() else []


def test_reply_gmail_freezes_a_gws_reply_and_shows_the_inherited_recipient_and_subject(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a Gmail original with a Reply-To
    calls = _reader(monkeypatch)
    # When: compose answers it in its own thread
    code = _cli(monkeypatch, "compose", "--reply-gmail", MESSAGE, "--body", BODY, "--cc", CC, "--no-post")
    # Then: one Gmail +reply draft, read through one metadata get, recipient and subject from the original
    assert code == 0
    [draft] = _drafts()
    assert draft["provider"] == "gmail" and draft["argv"] == [
        "gws", "gmail", "+reply", "--message-id", MESSAGE, "--body", BODY, "--cc", CC]
    assert draft["reply_inherit"] == {"source": "gmail", "message_id": MESSAGE,
                                      "to": "reply+thread@example.invalid",
                                      "subject": "Re: [repo] Issue #1814 opened"}
    assert [json.loads(argv[-1])["format"] for argv in calls] == ["metadata"]
    assert f"GMAIL-REPLY draft={draft['id']} account=gmail" in capsys.readouterr().out
    card = mail_card_compact.render(triage_core.render_approvals_message(
        draft, destination=triage_core.ApprovalRenderDestination.OWNER_DM), "")
    assert "받는 사람: `reply+thread@example.invalid`" in card
    assert "제목: `Re: [repo] Issue #1814 opened`" in card and BODY in card and f"Cc: `{CC}`" in card
    assert "sha256" not in card and "action hash" not in card and "+reply" not in card
    assert triage_core.shown(draft, "subject") == "Re: [repo] Issue #1814 opened"


def test_reply_gmail_without_reply_to_answers_the_sender_and_keeps_an_existing_re(
    env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reader(monkeypatch, headers=[{"name": "From", "value": "a@example.invalid"},
                                  {"name": "Subject", "value": "RE: 일정"}])
    assert _cli(monkeypatch, "compose", "--reply-gmail", MESSAGE, "--body", BODY, "--no-post") == 0
    [draft] = _drafts()
    assert draft["reply_inherit"]["to"] == "a@example.invalid"
    assert draft["reply_inherit"]["subject"] == "RE: 일정"


def test_reply_gmail_shows_every_reply_to_header_like_gws(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reader(monkeypatch, headers=[*HEADERS, {"name": "Reply-To", "value": "owner@example.invalid"}])
    assert _cli(monkeypatch, "compose", "--reply-gmail", MESSAGE, "--body", BODY, "--no-post") == 0
    assert _drafts()[0]["reply_inherit"]["to"] == "reply+thread@example.invalid, owner@example.invalid"


@pytest.mark.parametrize(("rc", "headers", "reason"), [
    (3, None, "gmail-read-failed"),
    (0, [{"name": "Subject", "value": "x"}], "no-sender"),
])
def test_reply_gmail_refuses_an_unreadable_original_before_any_draft(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    rc: int, headers: list | None, reason: str,
) -> None:
    _reader(monkeypatch, rc=rc, headers=headers)
    assert _cli(monkeypatch, "compose", "--reply-gmail", MESSAGE, "--body", BODY, "--no-post") == 2
    assert f"REPLY-REFUSED reason={reason}" in capsys.readouterr().err
    assert _drafts() == []


@pytest.mark.parametrize("extra", [
    ["--to", "x@example.invalid"], ["--subject", "x"], ["--bcc", "x@example.invalid"], ["--body", "  "],
])
def test_reply_gmail_refuses_what_gws_would_not_send_as_shown(
    env: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str],
) -> None:
    calls = _reader(monkeypatch)
    argv = ["compose", "--reply-gmail", MESSAGE, *(["--body", BODY] if extra[0] != "--body" else []), *extra, "--no-post"]
    assert _cli(monkeypatch, *argv) == 2
    assert _drafts() == [] and calls == []


def test_compose_without_reply_gmail_still_needs_to(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cli(monkeypatch, "compose", "--subject", "s", "--body", "b", "--no-post") == 2
    assert _drafts() == []


def _bound(draft: dict) -> dict:
    bound = triage_gate.set_approval_binding(
        draft, kind="reply", surface="owner-dm", channel_id="100000000000000002", policy_version=1)
    return triage_gate.set_message_id(bound, "gmail-reply-card", "100000000000000002")


@pytest.mark.parametrize("reaction", [triage_confirm.APPROVE_EMOJI, None])
def test_the_watcher_sends_the_frozen_reply_only_after_the_owner_check(
    env: Path, monkeypatch: pytest.MonkeyPatch, reaction: str | None,
) -> None:
    # Given: the posted Gmail reply draft and a fake gws
    _reader(monkeypatch)
    assert _cli(monkeypatch, "compose", "--reply-gmail", MESSAGE, "--body", BODY, "--no-post") == 0
    draft = _bound(_drafts()[0])
    sends: list[tuple[str, ...]] = []
    _install_fake_gws(monkeypatch, sends, env / "approvals.jsonl")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "resolve_reaction", lambda _draft: reaction)
    approval = triage_gate.Approval("reaction:gmail-reply-card", "manual_reaction", OWNER_ID)
    # When / Then: ✅ runs exactly the frozen reply once; no ✅ runs nothing
    if reaction is None:
        with pytest.raises(triage_gate.GateError):
            triage_gate.execute_draft(draft, approval)
        assert sends == []
        return
    triage_gate.execute_draft(draft, approval)
    assert sends == [("gws", "gmail", "+reply", "--message-id", MESSAGE, "--body", BODY)]


def test_an_edited_inherited_recipient_breaks_the_draft_hash(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reader(monkeypatch)
    assert _cli(monkeypatch, "compose", "--reply-gmail", MESSAGE, "--body", BODY, "--no-post") == 0
    [draft] = _drafts()
    draft["reply_inherit"] = {**draft["reply_inherit"], "to": "other@example.invalid"}
    with pytest.raises(triage_gate.GateError, match="해시 불일치"):
        triage_gate.execute_draft(draft, triage_gate.Approval("reaction:x", "manual_reaction", OWNER_ID))


def _posted_reply(body: str, thread: str) -> dict:
    import mail_gmail_send
    import triage_pipeline

    action = mail_gmail_send.build_action(mail_gmail_send.ReplyMailRequest(
        options=mail_gmail_send.DeliveryOptions(account="gmail"),
        reply_to_account="gmail", reply_message_id=MESSAGE, body=body))
    inherit = {"source": "gmail", "message_id": MESSAGE, "to": "reply+thread@example.invalid",
               "subject": "Re: [repo] Issue #1814 opened"}
    return triage_pipeline.gmail_reply_and_post(
        action, inherit, post=True, origin_channel_id=thread, origin_message_id="100000000000000888")


def test_the_posted_gmail_reply_card_resolves_on_the_owner_check_and_a_new_one_supersedes_it(owner_thread) -> None:
    from test_mail_card_v4_supersede import THREAD
    from triage_approval_reaction import resolve_reaction

    # Given: a Gmail reply card posted in the owner's request thread
    fake, patched = owner_thread
    old = _posted_reply("첫 답장", THREAD)
    content = fake.contents[old["message_id"]]
    # Then: the owner reads recipient, subject and body, never the draft id or hashes
    assert old["channel_id"] == THREAD and old["render_version"] == "5"
    assert "reply+thread@example.invalid" in content and "Re: [repo] Issue #1814 opened" in content
    assert "첫 답장" in content and old["id"] not in content and old["sha256"] not in content
    # When: a corrected reply is posted in the same thread before the owner decides
    new = _posted_reply("고친 답장", THREAD)
    # Then: the older card is closed and only the new card resolves on the owner's ✅
    assert [message for message, _ in patched] == [old["message_id"]]
    assert json.loads(triage_gate._draft_path(old["id"]).read_text(encoding="utf-8"))["status"] != "pending"
    fake.approved.add(new["message_id"])
    assert resolve_reaction(new) == triage_confirm.APPROVE_EMOJI

