from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
from automation.interop.approval_reminder_config import ApprovalReminderConfig  # noqa: E402


CHANNEL_ID = "1500000000000000001"
MESSAGE_ID = "1500000000000000003"
GUILD_ID = "1500000000000000009"


def test_mail_reminder_wiring_passes_guild_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: an old, pending mail approval draft stored under the temporary gate.
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    interop_config = tmp_path / "interop-config.json"
    interop_config.write_text('{"owner_id": "owner-1"}', encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(interop_config))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    draft = triage_gate.create_draft(
        uid="compose:test",
        sender="sender@example.test",
        mail_subject="subject",
        to="recipient@example.test",
        subject="subject",
        body="body",
        sensitive=False,
        tags=(),
        category="compose",
        flags=(),
        kind="compose",
        channel_id=CHANNEL_ID,
    )
    draft = triage_gate.set_message_id(
        draft, MESSAGE_ID, CHANNEL_ID, approval_created_at="2020-01-01T00:00:00Z"
    )
    fetch_calls: list[str] = []
    posts: list[tuple[str, str]] = []

    def fetch_channel(channel_id: str) -> dict[str, str | int]:
        fetch_calls.append(channel_id)
        return {"id": CHANNEL_ID, "type": 11, "guild_id": GUILD_ID}

    def post_approval_request(content: str, channel_id: str) -> str:
        posts.append((content, channel_id))
        return "1500000000000000077"

    monkeypatch.setattr(triage_confirm, "fetch_channel", fetch_channel)
    monkeypatch.setattr(triage_confirm, "post_approval_request", post_approval_request)
    monkeypatch.setattr(
        triage_confirm,
        "_api",
        lambda method, path, payload=None: (
            {"content": draft["sha256"]}
            if path.endswith(f"/messages/{MESSAGE_ID}")
            else {"id": CHANNEL_ID, "type": 1, "recipients": [{"id": "owner-1"}]}
        ),
    )
    monkeypatch.setattr(triage_confirm, "_reaction_users", lambda *args: [])

    # When: the real mail reminder entry point processes the pending draft.
    triage_cli._remind_pending(draft, ApprovalReminderConfig())

    # Then: exactly one reminder targets the bound channel and guild-qualified card.
    assert fetch_calls == [CHANNEL_ID]
    assert len(posts) == 1
    assert posts[0][1] == CHANNEL_ID
    assert f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{MESSAGE_ID}" in posts[0][0]
    assert "@me" not in posts[0][0]
