from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_digest  # noqa: E402
import triage_approval  # noqa: E402


def test_footer_names_the_owner_dm_through_the_central_formatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the policy formatter records its requested reply surface.
    calls: list[tuple[dict, bool]] = []

    def instruction(draft: dict, *, name_surface: bool = False) -> str:
        calls.append((draft, name_surface))
        return "이 메시지에 ✅ 실행 / ⛔ 취소 (소유자 DM)"

    monkeypatch.setattr(triage_approval, "reaction_instruction", instruction)

    # When: the digest builds its footer.
    footer = triage_digest._footer()

    # Then: it delegates destination naming to the policy formatter for the reply DM.
    assert calls == [({"kind": "reply", "surface": "owner-dm"}, True)]
    assert "소유자 DM" in footer
