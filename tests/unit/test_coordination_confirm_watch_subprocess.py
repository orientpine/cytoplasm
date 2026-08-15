from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "coordination" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import confirm_reaction_watch as watch  # noqa: E402
from coordination_pending import PendingConfirm  # noqa: E402


def test_finalize_child_receives_fallback_credential_when_parent_environment_lacks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    child = tmp_path / "coordinate_cli.py"
    result = child.with_suffix(".result")
    child_source = "\n".join(
        (
            "import os",
            "from pathlib import Path",
            "credential = os.environ.get('DISCORD_BOT_TOKEN', '')",
            "if not credential:",
            "    raise SystemExit(1)",
            "Path(__file__).with_suffix('.result').write_text(str(len(credential)), encoding='utf-8')",
        )
    )
    _ = child.write_text(child_source, encoding="utf-8")
    credential_hash = hashlib.sha256(b"coordination-watch-fixture").hexdigest()
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setattr(watch.io, "discord_bot_token", lambda: credential_hash)
    entry = PendingConfirm(
        draft_id="draft123",
        sha256="hash123",
        dm_channel_id="dm123",
        dm_message_id="msg123",
        slot="2026-07-20T09:00:00+09:00",
        summary="피어 미팅",
        correlation="coord123",
        duration_min=30,
        created=datetime(2026, 7, 19, tzinfo=UTC),
    )

    # When
    watch.CliCommands(child, tmp_path / "calendar_cli.py").finalize(entry)

    # Then
    assert result.read_text(encoding="utf-8") == str(len(credential_hash))
