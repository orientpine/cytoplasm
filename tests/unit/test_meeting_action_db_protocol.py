"""Keep structural action-item coverage apart from renderer characterization evidence."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence, get_type_hints

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "meeting" / "scripts"))

import meeting_action_db  # noqa: E402


class _PlainAction:
    title: str
    deadline: str | None
    owner: str
    basis: str

    def __init__(self, title: str, deadline: str | None, owner: str, basis: str) -> None:
        self.title = title
        self.deadline = deadline
        self.owner = owner
        self.basis = basis


def test_items_from_accepts_structural_action_items() -> None:
    mine = _PlainAction("내 항목", None, "", "내 근거")
    other = _PlainAction("외부 항목", "2026-09-01", "박", "외부 근거")

    assert meeting_action_db.items_from((mine,), (other,)) == (
        meeting_action_db.NewItem("내 항목", "나", "", "내 근거"),
        meeting_action_db.NewItem("외부 항목", "박", "2026-09-01", "외부 근거"),
    )
    assert get_type_hints(meeting_action_db.items_from)["todos"] == Sequence[
        meeting_action_db.ActionSource
    ]
