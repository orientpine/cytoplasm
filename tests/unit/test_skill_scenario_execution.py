"""Actually *runs* the skill scenarios that are provably side-effect-free.

`tests/unit/test_skill_scenario_drift.py` deliberately executes nothing: it
pins the drift that is visible statically (a scenario naming a path a migration
deleted, a scenario that stopped parsing) and its own guard asserts that the
module never invokes a scenario for real. That file is FS3-pinned — one added
line invalidates the recorded replay evidence — so the runtime half of the same
follow-up lives here instead of widening it.

The follow-up it closes: only 3 of 18 scenarios (calendar, mail, wiki, each in
its own dedicated test) run before deploy, so the remaining 15 first execute on
the peer node during `automation/deploy-skill.sh` stage 1 — the most expensive
place to discover a break. Dumping all 18 into the unit suite is not the fix:
most of them reach a real external-effect transport (Gmail send, Google Tasks
insert, Drive upload, an LLM CLI) and are offline *only* while an env-var stub
seam holds. This module therefore grows the covered set one skill at a time,
each run under a `mktemp` HOME (mode 700) with dummy credentials and a bounded
timeout, exactly the way deploy-skill.sh:853-858 shapes its sandbox.

Inventory — every `skills/*/scripts/scenario.sh`, classified by reading it.
SAFE means: no external-effect transport is reachable on any leg, every write
lands in a scenario-owned `mktemp -d`, and the executed path does not depend on
what happens to exist on the host. `_EXECUTED` / `_HELD_BACK` below are the
machine-consumed form of this table and must cover the on-disk set.

    | skill           | verdict | evidence                                        |
    |-----------------|---------|-------------------------------------------------|
    | budget          | UNSAFE  | Gmail send (`gws gmail +send`) behind BUDGET_GWS_BIN |
    | calendar        | UNSAFE  | Calendar insert/delete behind CALENDAR_GWS_BIN; already run by test_calendar_scenario.py |
    | coordination    | UNSAFE  | drives the Discord-capable coordinate_cli; green only because the tokenless refusal fires first |
    | doctype         | UNSAFE  | Codex OAuth CLI behind DOCTYPE_HERMES_BIN (the only tier) |
    | hello-autophagy | SAFE    | no seams at all: runs scripts/hello.sh and asserts its marker |
    | mail            | UNSAFE  | mailon send + two LLM CLIs behind TRIAGE_* seams; already run by test_mail_scenario.py |
    | meeting         | UNSAFE  | LLM extraction, offline only via --offline/--recorded-response |
    | patent-prep     | UNSAFE  | Drive upload + age encryption behind PATENT_GWS_BIN / PATENT_AGE_BIN |
    | plaud           | SAFE    | stdlib-only read of a mktemp state fixture; no transport, no writes outside mktemp |
    | procurement     | UNSAFE  | Drive upload (DRIVE_GWS_BIN) + Discord review DM (PROCURE_DISCORD_STUB) |
    | prompt          | UNSAFE  | root flips to /srv/autophagy-agents when that path exists — executed code path is host-dependent |
    | proposal        | UNSAFE  | Drive publish / image / refine transports, offline only via *_TRANSPORT=fake |
    | recall          | SAFE    | read-only skill; RECALL_FAKE_RESULTS / RECALL_FAKE_ERROR fixtures; writes confined to mktemp |
    | repair          | SAFE    | reads SKILL.md and asserts three substrings; no subprocess, no writes |
    | report          | UNSAFE  | reaches a Drive publish; the skip is decided by ambient import availability, not by the scenario |
    | speechtotext    | UNSAFE  | transcription API endpoint + whisper/ffmpeg behind SPEECHTOTEXT_*_BIN |
    | todo            | UNSAFE  | Google Tasks insert behind TODO_GWS_BIN |
    | topics          | SAFE    | local YAML state under mktemp; KNOWLEDGE_FAKE_PACK is a read-only evidence fixture |
    | wiki            | SAFE    | no effect transport, but already run by test_wiki_scenario.py with a staged INTEROP_RUNTIME |

Determinism: no sleeps and no polling. The only time bound is the subprocess
timeout, which turns a hung scenario into a failure instead of a hang.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_SCENARIOS: Final = tuple(sorted(_REPO.glob("skills/*/scripts/scenario.sh")))

# Every included scenario finishes in well under a second on the deploy host;
# the bound exists so a regression that blocks on a socket fails the suite.
_TIMEOUT_S: Final = 30.0

# The scenarios this module executes. Growing it is the point of the follow-up:
# add a skill only after reading it end to end and confirming the SAFE criteria
# in the table above.
_EXECUTED: Final = ("hello-autophagy", "plaud", "recall", "repair", "topics")

# Everything else, with the effect that keeps it out. Fail-closed: a new
# scenario that is in neither mapping fails `test_every_scenario_is_classified`
# rather than being silently skipped.
_HELD_BACK: Final[Mapping[str, str]] = {
    "budget": "Gmail send behind BUDGET_GWS_BIN",
    "calendar": "Calendar write behind CALENDAR_GWS_BIN",
    "coordination": "Discord request path, gated only by the tokenless refusal",
    "doctype": "Codex OAuth CLI behind DOCTYPE_HERMES_BIN",
    "mail": "mailon send behind TRIAGE_MAILON_PYTHON",
    "meeting": "LLM extraction, offline only via --recorded-response",
    "patent-prep": "Drive upload behind PATENT_GWS_BIN",
    "procurement": "Drive upload behind DRIVE_GWS_BIN",
    "prompt": "host-dependent root: /srv/autophagy-agents when present",
    "proposal": "Drive publish behind DRIVE_TRANSPORT=fake",
    "report": "Drive publish skipped by ambient import availability",
    "speechtotext": "transcription endpoint behind SPEECHTOTEXT_BASE_URL",
    "todo": "Google Tasks insert behind TODO_GWS_BIN",
    "wiki": "already executed by tests/unit/test_wiki_scenario.py",
}


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    """What one sandboxed scenario run produced."""

    returncode: int
    stdout: str
    stderr: str


def run_scenario_isolated(
    scenario: Path, home: Path, *, timeout_s: float = _TIMEOUT_S
) -> ScenarioOutcome:
    """Run one scenario the way deploy-skill.sh stage 1 does.

    The environment is built from nothing rather than inherited, so no ambient
    credential can reach the scenario and no ambient variable can decide which
    leg it takes. `home` doubles as the working directory: on the node the
    sandbox cwd belongs to another account, and several scenarios exist because
    that difference once broke them.
    """
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    completed = subprocess.run(
        ("/bin/bash", str(scenario)),
        check=False,
        capture_output=True,
        cwd=home,
        env={
            "AUTOPHAGY_DEMO_SECRET": "DUMMY-unit-scenario-execution",
            "AUTOPHAGY_REPO_ROOT": str(_REPO),
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
        },
        text=True,
        timeout=timeout_s,
    )
    return ScenarioOutcome(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


@pytest.mark.parametrize("skill", _EXECUTED)
def test_safe_scenario_passes_in_a_disposable_home(skill: str, tmp_path: Path) -> None:
    # Given: a scenario classified SAFE, and a HOME that holds nothing
    scenario = _REPO / "skills" / skill / "scripts" / "scenario.sh"

    # When: it runs with dummy credentials and no inherited environment
    outcome = run_scenario_isolated(scenario, tmp_path / "home")

    # Then: it reaches its own success marker, before deploy rather than during
    assert outcome.returncode == 0, outcome.stderr
    assert "SCENARIO-PASS" in outcome.stdout


def test_the_harness_reports_a_mutated_scenario_as_failing(tmp_path: Path) -> None:
    """The harness must bite: a broken skill has to come back non-zero.

    The mutation is the real drift this module exists to catch — the skill's
    own script stops honouring the contract its scenario asserts — applied to a
    throwaway copy so the repository tree is untouched.
    """
    # Given: a copy of a passing skill whose greeting no longer matches
    skill_copy = tmp_path / "skills" / "hello-autophagy"
    _ = shutil.copytree(_REPO / "skills" / "hello-autophagy", skill_copy)
    greeting = skill_copy / "scripts" / "hello.sh"
    _ = greeting.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
echo MUTATED-MARKER
""",
        encoding="utf-8",
    )

    mutated = run_scenario_isolated(skill_copy / "scripts" / "scenario.sh", tmp_path / "m")
    intact = run_scenario_isolated(
        _REPO / "skills" / "hello-autophagy" / "scripts" / "scenario.sh", tmp_path / "i"
    )

    # Then: the mutant fails loudly and the untouched original still passes
    assert mutated.returncode != 0
    assert "SCENARIO-FAIL" in mutated.stderr
    assert "SCENARIO-PASS" not in mutated.stdout
    assert intact.returncode == 0, intact.stderr


def test_the_harness_hands_over_no_inherited_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a credential in the parent process that no scenario may observe
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "DUMMY-parent-only")
    probe = tmp_path / "probe.sh"
    _ = probe.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
[[ -z "${DISCORD_BOT_TOKEN:-}" ]] || exit 9
[[ "$AUTOPHAGY_DEMO_SECRET" == DUMMY-* ]] || exit 8
[[ "$PWD" == "$HOME" ]] || exit 7
echo PROBE-PASS
""",
        encoding="utf-8",
    )

    # When: the probe runs through the same harness the scenarios use
    outcome = run_scenario_isolated(probe, tmp_path / "home")

    # Then: it saw a dummy secret, a disposable HOME, and no parent token
    assert outcome.returncode == 0, outcome.stderr
    assert "PROBE-PASS" in outcome.stdout


def test_every_scenario_is_classified() -> None:
    # Given: the scenarios actually on disk right now
    on_disk = {scenario.parents[1].name for scenario in _SCENARIOS}
    assert len(on_disk) >= 10, "empty glob would make this vacuous"

    # Then: each is either executed here or held back for a named effect, and
    # a newly added scenario fails this until somebody reads and classifies it
    classified = set(_EXECUTED) | set(_HELD_BACK)
    assert on_disk == classified
    assert set(_EXECUTED).isdisjoint(_HELD_BACK)
    assert all(reason for reason in _HELD_BACK.values())
