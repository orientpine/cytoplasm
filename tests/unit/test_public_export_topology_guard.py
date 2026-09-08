"""Fail-closed guard: no installation-specific topology reaches the public snapshot.

``configs/public-export-manifest.txt`` decides WHICH files are published; nothing decided
what VALUES they carry.  ``public_export_redaction.py`` rewrites three vendored mail files
and had no address or hostname rule at all, and gitleaks looks for secrets, not topology.
So an installation's tailnet address and production hostname reached the public repository
and were found by a third-party installer reading the code, not by a check (2026-09-07;
``docs/troubleshooting/신규-노드-설치-공백.md`` §5).

Deleting the values would close this instance and nothing else — the next hardcoded
address arrives the same way.  The guard is the fix; the deletions are its first passing
input.

Scope is measured, not assumed.  On the exported set the CGNAT range plus a hexadecimal
production-host pattern match six times with zero false positives, while RFC1918 matches a
dependency lockfile and a synthetic proposal corpus, and a loose ``ori[0-9a-z]+`` matches
1484 times (``origin``, ``orientpine``, ``original``).  A guard that cries wolf gets an
exception list, and an exception list is how this class comes back.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation.public_export_redaction import (
    PublicExportRedactionError,
    assert_no_private_topology,
    redact_vendor_tree,
)

_REPO: Final = Path(__file__).resolve().parents[2]
_MANIFEST: Final = _REPO / "configs" / "public-export-manifest.txt"

#: This module is published too, and the guard scans every published file — so a matching
#: literal here would make the guard refuse its own test.  That is not a reason for an
#: exception list; it is a reason for the fixtures to be assembled at run time and to be
#: SYNTHETIC: an unassigned address inside the shared-address range, and a hexadecimal
#: host belonging to no installation.  Writing either one whole would put real topology
#: back into the very tree this guard defends.
_SYNTHETIC_ADDRESS: Final = ".".join(("100", "64", "0", "1"))
_SYNTHETIC_HOST: Final = "ori" + "beef"


def _exclusions() -> tuple[str, ...]:
    lines = _MANIFEST.read_text(encoding="utf-8").splitlines()
    return tuple(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def _is_excluded(relative: str, exclusions: tuple[str, ...]) -> bool:
    return any(
        relative.startswith(entry) if entry.endswith("/") else relative == entry
        for entry in exclusions
    )


def _materialize_public_snapshot(destination: Path) -> None:
    """Reproduce public_export.sh: whole tree, minus manifest entries, then redaction."""
    tracked = subprocess.run(
        ("git", "ls-files"), cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    if not tracked:
        # public_export.sh runs `pytest tests/unit` inside the EXPORTED tree, whose index
        # it has just emptied.  Passing there would be vacuous, and asserting there would
        # block the release, so say plainly that this environment cannot answer.
        pytest.skip("not a source checkout: nothing to snapshot")
    exclusions = _exclusions()
    for relative in tracked:
        if not relative or _is_excluded(relative, exclusions):
            continue
        source = _REPO / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    redact_vendor_tree(destination)


def test_the_public_snapshot_carries_no_installation_topology(tmp_path: Path) -> None:
    # Given: the snapshot public_export.sh would publish from this working tree.
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    _materialize_public_snapshot(snapshot)

    # When/Then: the guard the exporter runs finds no address or host to leak.
    assert_no_private_topology(snapshot)


def test_a_tailnet_address_is_refused_with_its_location(tmp_path: Path) -> None:
    # Given: a snapshot file carrying a tailnet address.
    leak = tmp_path / "automation" / "probe.sh"
    leak.parent.mkdir(parents=True)
    _ = leak.write_text(f'url="http://{_SYNTHETIC_ADDRESS}:8800/"\n', encoding="utf-8")

    # When: the exporter's guard runs over that snapshot.
    with pytest.raises(PublicExportRedactionError) as error:
        assert_no_private_topology(tmp_path)

    # Then: it names the path so the operator fixes the source, not the snapshot.
    assert "automation/probe.sh" in str(error.value)


def test_a_production_hostname_is_refused(tmp_path: Path) -> None:
    # Given: a snapshot file carrying a node hostname of the production shape.
    leak = tmp_path / "tests" / "probe_test.py"
    leak.parent.mkdir(parents=True)
    _ = leak.write_text(f'NODE = "{_SYNTHETIC_HOST}"\n', encoding="utf-8")

    # When/Then: the guard refuses it by the same rule.
    with pytest.raises(PublicExportRedactionError):
        assert_no_private_topology(tmp_path)


def test_words_beginning_with_ori_and_public_addresses_are_not_topology(tmp_path: Path) -> None:
    # Given: the vocabulary a loose pattern would have flagged 1484 times.
    ordinary = tmp_path / "automation" / "ordinary.py"
    ordinary.parent.mkdir(parents=True)
    _ = ordinary.write_text(
        'REMOTE = "origin"\n'
        'OWNER = "orientpine"\n'
        '# the original loopback binding is 127.0.0.1 and 0.0.0.0 means every interface\n'
        'VERSION = "10.4.0.35"\n',
        encoding="utf-8",
    )

    # When/Then: none of it is installation topology.
    assert_no_private_topology(tmp_path)


def test_unreadable_bytes_do_not_defeat_the_guard(tmp_path: Path) -> None:
    # Given: a binary artifact beside a leaking text file.
    binary = tmp_path / "docs" / "evidence.gz"
    binary.parent.mkdir(parents=True)
    _ = binary.write_bytes(b"\x1f\x8b\x08\x00leak-" + _SYNTHETIC_ADDRESS.encode())
    text = tmp_path / "docs" / "note.md"
    _ = text.write_text(f"host {_SYNTHETIC_HOST}\n", encoding="utf-8")

    # When/Then: an undecodable neighbour does not silence the readable leak.
    with pytest.raises(PublicExportRedactionError) as error:
        assert_no_private_topology(tmp_path)
    assert "docs/note.md" in str(error.value)
