"""E2E-mode owner DMs carry a `[E2E] ` prefix; production DMs never do."""

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


def test_send_owner_dm_prefixes_in_e2e_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture_sent(monkeypatch)
    monkeypatch.setenv("E2E_TEST_MODE", "1")

    channel_id, message_id = lifecycle.send_owner_dm(
        "owner-1", "✅ 일정 조율 완료 (coord-abc): 테스트 — 슬롯. 캘린더에 등록되었습니다."
    )

    assert (channel_id, message_id) == ("chan-1", "msg-1")
    assert sent == [
        ("chan-1", "[E2E] ✅ 일정 조율 완료 (coord-abc): 테스트 — 슬롯. 캘린더에 등록되었습니다.")
    ]


@pytest.mark.parametrize("mode", [None, "0", ""])
def test_send_owner_dm_stays_unprefixed_outside_e2e(
    monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    sent = _capture_sent(monkeypatch)
    if mode is None:
        monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    else:
        monkeypatch.setenv("E2E_TEST_MODE", mode)

    lifecycle.send_owner_dm("owner-1", "🚫 일정 조율 종료 (coord-abc)")

    assert sent == [("chan-1", "🚫 일정 조율 종료 (coord-abc)")]
