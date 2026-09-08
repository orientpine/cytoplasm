"""Machine form of the boundary rule: the render engine ships in this repository.

This file replaces the pin-agreement contract. A pin only meant something while the
engine lived in another checkout at a fixed SHA — it named which foreign commit was
allowed to run. The engine is in-tree now, so the contract that stands in its place is
that no pin machinery survives in shipped code or configuration, and that the engine a
reader finds here is the one that renders.

The scan covers the shipped trees only. Tests are deliberately out of scope because a
test that *proves the removal* has to name what it asserts is gone — forbidding that
would forbid the evidence. Tests that still *use* the retired fields cannot hide either:
``ProposalConfig`` no longer carries them, so such a test dies with ``TypeError`` when the
suite runs. Prose keeps its own history and is not machinery.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
ENGINE_ROOT: Final = REPO_ROOT / "skills" / "proposal" / "engine"
SEARCHED_TREES: Final = ("skills", "automation", "configs")
BINARY_SUFFIXES: Final = frozenset({".png", ".jpg", ".jpeg", ".pdf", ".hwpx", ".zip", ".gz"})

_RETIRED: Final = re.compile(
    r"PROPOSAL_DOCBOT_PIN|PROPOSAL_DOCBOT_ROOT|PROPOSAL_DOCBOT_SOURCE|"
    r"ENGINE-PIN-BLOCK|docbot_root|docbot_pin"
)

# A file may name a retired marker for exactly one reason: to enforce that it never
# appears. Registering that here keeps the exception visible instead of weakening the
# scan into something that cannot tell absence-enforcement from a survival.
_ABSENCE_ASSERTIONS: Final = {
    "skills/proposal/scripts/scenario.sh": (
        "the sandbox greps for the marker to fail if the render ever consults an "
        "external engine pin again"
    ),
}


def _tracked_text_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", *SEARCHED_TREES],
        capture_output=True,
        text=True,
        check=True,
    )
    here = Path(__file__).resolve()
    paths: list[Path] = []
    for line in listed.stdout.splitlines():
        if not line:
            continue
        path = REPO_ROOT / line
        if path.resolve() == here or path.suffix in BINARY_SUFFIXES or not path.is_file():
            continue
        paths.append(path)
    return paths


def test_no_external_engine_pin_machinery_survives() -> None:
    offenders: list[str] = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = str(path.relative_to(REPO_ROOT))
        if _RETIRED.search(text) and relative not in _ABSENCE_ASSERTIONS:
            offenders.append(relative)

    assert not offenders, f"external-engine pin machinery survives in: {offenders}"


@pytest.mark.skipif(
    not ENGINE_ROOT.is_dir(),
    reason="skills/proposal/engine is manifest-excluded from the public export; this contract holds only where the engine ships",
)
def test_the_engine_ships_in_this_repository() -> None:
    assert ENGINE_ROOT.is_dir(), f"in-tree render engine missing at {ENGINE_ROOT}"
    assert sorted(ENGINE_ROOT.rglob("*.py")), "engine carries no sources"
    assert sorted(ENGINE_ROOT.glob("resource/*.hwpx")), "engine ships no form to render into"


def test_every_registered_absence_assertion_still_asserts_absence() -> None:
    for relative, reason in _ABSENCE_ASSERTIONS.items():
        path = REPO_ROOT / relative
        assert path.is_file(), f"{relative} is registered but absent"
        assert reason.strip(), f"{relative} is registered without a reason"
        text = path.read_text(encoding="utf-8")
        assert _RETIRED.search(text), (
            f"{relative} no longer names a retired marker; drop its exemption"
        )
        assert "fail " in text, f"{relative} names the marker without failing on it"
