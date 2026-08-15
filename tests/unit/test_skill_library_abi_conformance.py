"""A mounted skill snapshot must still be able to call the shared library it imports.

AS-3.2 removed ``approval_env_var`` from ``DiscordChannelDirectory``, but three
live skill snapshots — mail, budget, patent-prep — were frozen at a commit that
still passed that kwarg. The library moves under a live snapshot on any unrelated
deploy (``deploy-skill.sh``'s first step is an ops ``git pull``), so the new
module raises ``TypeError`` when the skill's binding helper runs — a live
approval flow that silently refuses to post. Only a human caught it.

The break is INSIDE a function (``directory_module.DiscordChannelDirectory(...)``),
so a plain import smoke-test never sees it. This checker walks the snapshot's AST,
resolves each ``_repo_module("x").Symbol(...)`` call against the CURRENT library
signature, and binds the call site — reporting a mismatch, or conservatively
SKIPPING what it cannot judge statically. A checker that cries wolf gets disabled,
so the skip list is as load-bearing as the violation list.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Load-bearing import order: the inventory module puts the repo root on sys.path.
from approval_conformance_inventory import _REPO

from automation.skill_library_abi import SkipReason, check_snapshot

_LIBRARY = _REPO / "automation"


def _snapshot(tmp_path: Path, body: str) -> Path:
    """A mounted-release layout: releases/<skill>/<hash>/scripts/<name>.py."""
    scripts = tmp_path / "releases" / "demo" / "deadbeef" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "demo_binding.py").write_text(body, encoding="utf-8")
    return scripts.parent


_AS_R3_BREAK = '''
import importlib, os, sys
from pathlib import Path

def repo_root():
    return Path(os.environ.get("AUTOPHAGY_REPO_ROOT", "/srv/autophagy-agents"))

def _repo_module(name):
    sys.path.insert(0, str(repo_root()))
    return importlib.import_module(f"automation.interop.{name}")

def directory():
    module = _repo_module("approval_directory")
    return module.DiscordChannelDirectory(
        token="t", owner_id="o", approval_env_var="TRIAGE_APPROVALS_CHANNEL_ID"
    )
'''

_KWARGS_SPLAT = '''
import importlib, os, sys
from pathlib import Path

def _repo_module(name):
    return importlib.import_module(f"automation.interop.{name}")

def directory(payload):
    module = _repo_module("approval_directory")
    return module.DiscordChannelDirectory(**payload)
'''


def test_flags_the_as_r3_break(tmp_path: Path) -> None:
    # Given: a snapshot passing the retired approval_env_var kwarg.
    report = check_snapshot(_snapshot(tmp_path, _AS_R3_BREAK), _LIBRARY)

    # Then: exactly one violation, naming the offending kwarg — and no false skip.
    assert len(report.violations) == 1, report
    violation = report.violations[0]
    assert violation.symbol == "DiscordChannelDirectory"
    assert "approval_env_var" in violation.detail


def test_current_repo_skills_are_clean(tmp_path: Path) -> None:
    """The false-positive floor: the checker must pass the repo's own live code.

    A checker that flags the shipping skills against the shipping library is
    unusable and will be turned off — which is exactly how the guard dies.
    """
    del tmp_path
    violations: list[str] = []
    for scripts in sorted((_REPO / "skills").glob("*/scripts")):
        report = check_snapshot(scripts.parent, _LIBRARY)
        violations += [f"{v.snapshot.name}:{v.qualname} {v.symbol} {v.detail}" for v in report.violations]
    assert violations == [], "checker flags currently-shipping skills:\n" + "\n".join(violations)


def test_kwargs_call_is_skipped_not_violated(tmp_path: Path) -> None:
    # Given: a call whose keyword names are unknown until runtime.
    report = check_snapshot(_snapshot(tmp_path, _KWARGS_SPLAT), _LIBRARY)

    # Then: it is recorded as skipped, never as a violation — unknown names are
    # unjudgeable, and guessing would be the false positive that kills the tool.
    assert report.violations == ()
    assert any(
        skip.symbol == "DiscordChannelDirectory" and skip.reason is SkipReason.DYNAMIC_KWARGS
        for skip in report.skipped
    ), report.skipped


def test_direct_import_call_is_out_of_scope_not_violated(tmp_path: Path) -> None:
    # Given: a call whose module came from a plain importlib.import_module, not the
    # _repo_module convention the checker models.
    body = (
        "def directory():\n"
        "    import importlib\n"
        "    mod = importlib.import_module('automation.interop.approval_directory')\n"
        "    return mod.DiscordChannelDirectory(token='t', owner_id='o', bogus=1)\n"
    )
    report = check_snapshot(_snapshot(tmp_path, body), _LIBRARY)

    # Then: outside the modelled coupling ⇒ never a false violation. Guessing at
    # untraced call shapes is exactly the false positive that gets the tool disabled.
    assert report.violations == ()


@pytest.mark.parametrize("missing", ["", "nonexistent-dir"])
def test_absent_snapshot_scripts_yield_an_empty_clean_report(tmp_path: Path, missing: str) -> None:
    # Given / When / Then: a snapshot with no scripts is clean, never a crash.
    report = check_snapshot(tmp_path / missing, _LIBRARY)
    assert report.violations == ()
    assert report.skipped == ()
