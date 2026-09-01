"""Machine form of the pin rule stated in prose by `docs/guide/kd-at-두-저장소-운영.md`.

A pin that disagrees with itself points at an engine the render rejects with
`ENGINE-PIN-BLOCK`, so the three declarations must move together.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]

PIN_SOURCES: Final = (
    Path("configs/env.example"),
    Path("skills/proposal/SKILL.md"),
    Path("docs/기능소개/연구계획서-자동생성.md"),
)

_PIN_RE: Final = re.compile(r"PROPOSAL_DOCBOT_PIN=([0-9a-f]{40})")
_PLACEHOLDER: Final = "0" * 40


def _declared_pins(path: Path) -> list[str]:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    return [pin for pin in _PIN_RE.findall(text) if pin != _PLACEHOLDER]


def test_every_documented_place_declares_the_engine_pin() -> None:
    missing = [str(path) for path in PIN_SOURCES if not _declared_pins(path)]
    assert not missing, f"no engine pin found in: {missing}"


def test_the_engine_pin_is_the_same_everywhere_it_is_written() -> None:
    declared = {str(path): sorted(set(_declared_pins(path))) for path in PIN_SOURCES}
    distinct = sorted({pin for pins in declared.values() for pin in pins})
    assert len(distinct) == 1, f"engine pin disagrees with itself: {declared}"


def test_the_sandbox_scenario_keeps_its_placeholder_pin() -> None:
    scenario = (REPO_ROOT / "skills/proposal/scripts/scenario.sh").read_text(encoding="utf-8")
    assert f"PROPOSAL_DOCBOT_PIN={_PLACEHOLDER}" in scenario
