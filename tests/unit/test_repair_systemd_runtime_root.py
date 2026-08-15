"""The repair units must execute from the immutable release runtime, not the
mutable deploy checkout (DG-5).

DG-5 moves the runtime off /srv/autophagy-agents onto /srv/autophagy-agent-current
(the release symlink). WorkingDirectory / PYTHONPATH / ExecStart migrate; the
ReadWritePaths safety property (repair units cannot write the mirror) is a
SEPARATE invariant preserved byte-identical — see test_repair_systemd_readwrite_paths.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config

_SYSTEMD: Final = Path(__file__).resolve().parents[2] / "automation" / "repair" / "systemd"
_RELEASE_CURRENT: Final = "/srv/autophagy-agent-current"
_MIRROR: Final = "/srv/autophagy-agents"
_UNITS: Final = (
    "autophagy-repair-agent.service",
    "autophagy-repair-approval-watch.service",
)


def _directive(unit: str, key: str) -> str:
    text = render_asset(_SYSTEMD / unit, default_node_config())
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    assert match is not None, f"{unit} has no {key}= line"
    return match.group(1)


@pytest.mark.parametrize("unit", _UNITS)
def test_working_directory_is_the_release_runtime(unit: str) -> None:
    assert _directive(unit, "WorkingDirectory") == _RELEASE_CURRENT


@pytest.mark.parametrize("unit", _UNITS)
def test_pythonpath_is_the_release_runtime(unit: str) -> None:
    assert _directive(unit, "Environment") == f"PYTHONPATH={_RELEASE_CURRENT}"


@pytest.mark.parametrize("unit", _UNITS)
def test_execstart_runs_from_the_release_runtime(unit: str) -> None:
    execstart = _directive(unit, "ExecStart")
    assert f"{_RELEASE_CURRENT}/automation/repair/" in execstart
    assert f"{_MIRROR}/automation" not in execstart


@pytest.mark.parametrize("unit", _UNITS)
def test_no_directive_still_executes_from_the_mirror(unit: str) -> None:
    # WorkingDirectory/PYTHONPATH/ExecStart must not reference the mirror as a
    # code root. (ReadWritePaths legitimately references neither the mirror nor
    # current — it lists the private repair state dirs; that is checked elsewhere.)
    for key in ("WorkingDirectory", "Environment", "ExecStart"):
        assert _MIRROR not in _directive(unit, key), f"{unit} {key} still points at the mirror"


@pytest.mark.parametrize("unit", _UNITS)
def test_readwrite_paths_are_unchanged_byte_for_byte(unit: str) -> None:
    # DG-5 must not touch the RWP safety property (7ea6a8c). Pin the exact list.
    expected = (
        "/srv/autophagy-repair-work /srv/autophagy-private/repair-logs "
        "/srv/autophagy-private/repair-plans /srv/autophagy-private/repair-approvals.jsonl "
        "/srv/autophagy-private/repair-approval-pending /srv/autophagy-private/repair-state"
    )
    assert _directive(unit, "ReadWritePaths") == expected
