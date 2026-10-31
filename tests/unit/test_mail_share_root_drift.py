"""A share folder frozen in a card must be the one the send step accepts (2026-10-31).

ec6197: the YooDori gate started compose without DRIVE_OUTPUTS_ROOT, so the card froze the default
root, the owner pressed ✅, and only then the watcher, which reads the setting, refused the folder.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pytest

from automation import drive_outputs
from automation.interop import origin_notice
from automation.yoodori_gate import yoodori_request_runtime as runtime
from tests.unit import test_mail_single_live_request as mail
from tests.unit.test_mail_card_v4_supersede import THREAD, _post
from tests.unit.test_mail_single_live_request import mail_env as mail_env

import mail_send_retry  # noqa: E402
import triage_supersede  # noqa: E402

ROOT = "Dori's Home"
OLD_ROOT = "autophagy"


def _spec(root: str) -> dict:
    return {"drive_name": "2026-10-31_합본.mp4", "folder": [root, "공유", "2026"], "frozen_path": "/nonexistent/f",
            "grantees": ["viewer@example.invalid"], "placeholder": "<Drive 링크>", "role": "reader",
            "sha256": "0" * 64, "size_bytes": 10, "source_name": "합본.mp4"}


def _home(tmp_path: Path) -> Path:
    (tmp_path / ".env.secrets").write_text(
        f'DISCORD_BOT_TOKEN=x\nMAILON_PW=hidden\nDRIVE_OUTPUTS_ROOT="{ROOT}"\n', encoding="utf-8")
    return tmp_path


def test_the_folder_a_gate_card_freezes_passes_the_send_check(tmp_path: Path, monkeypatch) -> None:
    home = _home(tmp_path)
    card = runtime.card_env(home, Path("/release"), {"HOME": str(home)})
    assert "MAILON_PW" not in card
    monkeypatch.delenv("DRIVE_OUTPUTS_ROOT", raising=False)
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", card["DRIVE_OUTPUTS_ROOT"])
    parts, _name = drive_outputs.share_target("합본.mp4", on=date(2026, 10, 31))
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", runtime.job_env(home, Path("/release"), {})["DRIVE_OUTPUTS_ROOT"])
    drive_outputs.require_share_folder(parts)
    assert parts[0] == ROOT


@pytest.fixture
def card(mail_env, monkeypatch: pytest.MonkeyPatch):
    fake = mail_env[0]
    fake.threads[THREAD] = "합본 메일"
    posts: list[tuple[str, str, dict | None]] = []
    original = fake.api

    def api(method, path, payload=None):
        if method in {"POST", "PATCH"}:
            posts.append((method, path, payload))
        if method == "PATCH":
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                fake.contents[parts[3]] = str((payload or {}).get("content", ""))
            return {"id": parts[1]}
        return original(method, path, payload)

    monkeypatch.setattr(mail.triage_confirm, "_api", api)
    monkeypatch.setattr(mail.triage_confirm, "_origin_notice", lambda: origin_notice)
    monkeypatch.setattr(mail.triage_confirm, "_dm_transport",
                        lambda channel: mail.triage_confirm._CardReply(channel, ""))
    monkeypatch.setattr(mail.triage_cli, "_digest_reactions", lambda: None)
    monkeypatch.setattr(mail.triage_cli.triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(mail.triage_cli, "_remind_pending", lambda _draft, _config: None)
    sends: list[object] = []
    monkeypatch.setattr(mail.triage_gate, "_run_send", lambda argv: sends.append(argv))
    monkeypatch.setattr(drive_outputs, "client_from_environment",
                        lambda: pytest.fail("a moved card must not touch Drive"))
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", ROOT)

    def make(root: str) -> dict:
        stored = _post(mail.triage_gate.create_draft(
            uid=f"compose:{root}", sender="", mail_subject="", to="peer@example.invalid", cc="",
            subject="영상 합본", body="다운로드: <Drive 링크>", category="compose", flags=(), kind="compose",
            origin_channel_id=THREAD, origin_message_id="100000000000000888", drive_share=_spec(root)))
        posts.clear()
        return stored

    return fake, make, posts, sends, mail_env[2]


def _watch() -> int:
    return mail.triage_cli.cmd_watch(argparse.Namespace())


def test_a_card_whose_folder_moved_closes_before_the_owner_decides(card, capsys) -> None:
    fake, make, posts, sends, drafts = card
    stored = make(OLD_ROOT)
    assert _watch() == 0
    record = mail._stored(drafts, stored["id"])
    assert (record["status"], record["expired_reason"]) == ("expired", "drive-share-folder-moved")
    assert sends == [] and "새 카드" in fake.contents[stored["message_id"]]
    assert f"EXPIRED draft={stored['id']} reason=drive-share-folder-moved" in capsys.readouterr().out
    assert any(method == "POST" and "유돌에게 새 카드" in str(payload) for method, _p, payload in posts)


def test_a_card_in_the_current_root_waits_for_the_owner(card) -> None:
    _fake, make, _posts, sends, drafts = card
    stored = make(ROOT)
    assert _watch() == 0
    assert mail._stored(drafts, stored["id"])["status"] == "pending" and sends == []


def test_a_moved_card_the_owner_already_approved_is_left_to_the_watcher(card) -> None:
    fake, make, _posts, _sends, drafts = card
    stored = make(OLD_ROOT)
    fake.approved.add(stored["message_id"])
    assert triage_supersede.close_moved_share(stored) is False
    assert mail._stored(drafts, stored["id"])["status"] == "pending"


def test_the_owner_reads_why_a_refused_folder_stopped_the_mail() -> None:
    text = mail_send_retry.failure_text(
        {"subject": "s", "to": "t", "id": "i", "last_error": {
            "error_code": "drive_share_failed", "stage": "drive_share", "cause": "drive_folder_refused"}}, "x")
    assert "유돌에게 새 카드" in text
