"""E2E owner notices can go to a test log instead of the owner's DM.

Kept apart from test_coordination_e2e_prefix.py so that file's recorded output
stays reproducible. The weekly bank sets COORDINATION_E2E_NOTICE_LOG so its
coordination runs stop paging the owner (2026-10-10: nine [E2E] DMs in one day).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "coordination" / "scripts"))

import coordinate_io  # noqa: E402
import coordination_lifecycle as lifecycle  # noqa: E402


def _capture_sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(coordinate_io, "owner_approval_channel", lambda owner_id: "chan-1")

    def _post(channel_id: str, content: str) -> str:
        sent.append((channel_id, content))
        return "msg-1"

    monkeypatch.setattr(coordinate_io, "post_message", _post)
    return sent


def test_e2e_notice_goes_to_the_log_and_not_to_the_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sent = _capture_sent(monkeypatch)
    log = tmp_path / "notices.log"
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv(lifecycle.E2E_NOTICE_LOG_ENV, str(log))

    lifecycle.send_owner_dm("owner-1", "🚫 일정 조율 종료 (coord-abc): 상대측 거절")

    assert sent == []
    assert log.read_text(encoding="utf-8") == "[E2E] 🚫 일정 조율 종료 (coord-abc): 상대측 거절\n"
    assert log.stat().st_mode & 0o777 == 0o600


def test_notice_log_is_ignored_outside_e2e(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sent = _capture_sent(monkeypatch)
    log = tmp_path / "notices.log"
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setenv(lifecycle.E2E_NOTICE_LOG_ENV, str(log))

    lifecycle.send_owner_dm("owner-1", "🚫 일정 조율 종료 (coord-abc)")

    assert sent == [("chan-1", "🚫 일정 조율 종료 (coord-abc)")]
    assert not log.exists()
