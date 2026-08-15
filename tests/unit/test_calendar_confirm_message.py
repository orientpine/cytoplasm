from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))

calendar_confirm = import_module("calendar_confirm")


def test_confirmation_message_instructs_owner_to_react_on_that_dm_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    messages: list[str] = []

    def fake_api(method: str, path: str, payload: dict[str, str] | None = None):
        if method == "POST" and path == "/channels/dm123/messages":
            assert payload is not None
            messages.append(payload["content"])
            return {"id": "msg123"}
        return None

    draft = {
        "id": "draft123",
        "sha256": "hash123",
    }
    monkeypatch.setattr(calendar_confirm, "_change_summary", lambda _draft: "CHANGE-SUMMARY")
    monkeypatch.setattr(calendar_confirm, "_api", fake_api)

    # When
    calendar_confirm.post_confirmation_message(draft, "dm123")

    # Then
    assert "이 메시지에 ✅ 실행 / ⛔ 취소" in messages[0]
    assert calendar_confirm.APPROVE_EMOJI in messages[0]
    assert calendar_confirm.CANCEL_EMOJI in messages[0]
    assert "approvals" not in messages[0]
