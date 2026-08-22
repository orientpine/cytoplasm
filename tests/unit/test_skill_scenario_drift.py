"""Skill scenarios must not silently fall behind a migration.

The AS R1 rollout was blocked by the sandbox five times and every block was a
real defect (staging list not updated, an eager import for display text, an
`AUTOPHAGY_REPO_ROOT` overwrite regression, a secret-scan false positive, a
non-snowflake fixture). Guards were added for each, but the shared cause was
that **a scenario can drift behind a migration without anything saying so until
deploy time** — the most expensive place to find out.

Running all seventeen scenarios inside the unit suite is not the answer: they
are shell harnesses with their own runtimes, and three of them already have
dedicated tests. What is cheap, deterministic and side-effect-free is the class
of drift that is visible without executing anything — a scenario referring to a
repository path that a migration moved or deleted, or a scenario that stopped
parsing at all. That is what this pins.

The remaining gap (only 3 of 17 scenarios are actually executed before deploy)
is recorded in `docs/follow-ups.md`; closing it means writing per-skill runtime
harnesses, not widening this check.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[2]
_SCENARIOS: Final = sorted(_ROOT.glob("skills/*/scripts/scenario.sh"))
# Repository-relative paths as they appear inside a scenario. Interpolated or
# globbed values are skipped: they resolve at runtime, not statically. The
# lookbehind keeps `$REPO/automation/x.py` out — without it the tail of an
# interpolated path is indistinguishable from a literal one.
_REPO_PATH: Final = re.compile(
    r"(?<![\w$/.-])(?:automation|skills|configs|tests|prompts|docs)/[A-Za-z0-9_./-]+"
)
_DYNAMIC: Final = ("$", "*", "?")


def scenario_path_drift(text: str, root: Path) -> tuple[str, ...]:
    """Repository paths a scenario names that no longer exist."""
    drifted: list[str] = []
    for match in sorted(set(_REPO_PATH.findall(text))):
        candidate = match.rstrip(".:,;)\"'")
        if any(marker in candidate for marker in _DYNAMIC):
            continue
        if not (root / candidate).exists():
            drifted.append(candidate)
    return tuple(drifted)


def test_the_scenario_set_is_not_empty() -> None:
    # Given — an empty glob would make every assertion below vacuously true
    assert len(_SCENARIOS) >= 10


def test_no_scenario_references_a_path_a_migration_removed() -> None:
    drifted = {
        scenario.relative_to(_ROOT).as_posix(): scenario_path_drift(
            scenario.read_text(encoding="utf-8"), _ROOT
        )
        for scenario in _SCENARIOS
    }

    assert {name: paths for name, paths in drifted.items() if paths} == {}


def test_every_scenario_still_parses() -> None:
    broken = [
        scenario.relative_to(_ROOT).as_posix()
        for scenario in _SCENARIOS
        if subprocess.run(
            ("bash", "-n", str(scenario)), check=False, capture_output=True
        ).returncode
        != 0
    ]

    assert broken == []


def test_the_drift_check_catches_an_injected_migration(tmp_path: Path) -> None:
    # Given a scenario that names a module a migration deleted
    text = 'python3 automation/gone_after_migration.py --check\n'

    # When / Then — the checker must actually bite, not just pass everything
    assert scenario_path_drift(text, _ROOT) == ("automation/gone_after_migration.py",)
    assert scenario_path_drift("python3 automation/healthcheck.sh\n", _ROOT) == ()
    del tmp_path


def test_runtime_interpolated_paths_are_not_reported(tmp_path: Path) -> None:
    # Given — "$AUTOPHAGY_REPO_ROOT/automation/x.py" resolves at run time
    del tmp_path

    assert scenario_path_drift('"$REPO/automation/x.py"\n', _ROOT) == ()
    assert scenario_path_drift("automation/*.py\n", _ROOT) == ()


def test_nothing_in_this_module_executes_a_scenario() -> None:
    # Given — a scenario may send mail or open a browser; only `bash -n` is safe
    source = Path(__file__).read_text(encoding="utf-8")

    invocations = [line for line in source.splitlines() if "subprocess.run(" in line]

    assert len(invocations) == 2  # the one real call, and this guard's own scan
    assert '("bash", "-n", str(scenario))' in source
