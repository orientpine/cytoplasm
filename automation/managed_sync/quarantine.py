"""Quarantine staging for verified managed-skill releases (MS-S4).

Copies a verified release tree into ``quarantine/<skill>/<digest>/<skill>/``
(Codified decision 10 — the inner basename equals the skill so activation can
feed it straight to ``SKILL_SRC_DIR``) and writes the canonical manifest plus
a provenance record beside the inner tree. Automatic stages STOP here (SI-1):
this module never touches live skill storage and never activates anything.

E9-class sanity limits are re-applied at staging time. The constants are
mirrored BY VALUE from ``automation/skill_store.py`` (Codified decision 7
keeps that helper import-free, so they cannot be imported from it); a
drift-guard test compares the values.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.managed_skills.manifest import canonical_json

from .verify import VerifiedRelease

_MAX_ARCHIVE_BYTES: Final = 64 * 1024 * 1024
_MAX_MEMBERS: Final = 10_000
_PRIVATE_DIR_MODE: Final = 0o700


class QuarantineError(Exception):
    """A verified release could not be staged into quarantine safely."""


def _ensure_sane_tree(tree: Path) -> None:
    """Re-apply E9-class sanity limits to the verified tree (fail-closed)."""
    if tree.is_symlink() or not tree.is_dir():
        raise QuarantineError(f"verified tree is not a plain directory: {tree.name}")
    members = 0
    total_bytes = 0
    for directory, dirnames, filenames in os.walk(tree):
        base = Path(directory)
        for name in dirnames:
            if (base / name).is_symlink():
                raise QuarantineError(f"symlink in verified tree: {name}")
        for name in filenames:
            info = (base / name).lstat()
            if stat.S_ISLNK(info.st_mode):
                raise QuarantineError(f"symlink in verified tree: {name}")
            if not stat.S_ISREG(info.st_mode):
                raise QuarantineError(f"non-regular file in verified tree: {name}")
            members += 1
            total_bytes += info.st_size
    if members > _MAX_MEMBERS:
        raise QuarantineError(f"verified tree exceeds {_MAX_MEMBERS} members")
    if total_bytes > _MAX_ARCHIVE_BYTES:
        raise QuarantineError(f"verified tree exceeds {_MAX_ARCHIVE_BYTES} bytes")


def _write_metadata(staging_dir: Path, verified: VerifiedRelease) -> None:
    _ = (staging_dir / "manifest.json").write_text(
        canonical_json(verified.manifest) + "\n", encoding="utf-8"
    )
    provenance = {
        "publisher": verified.manifest.publisher,
        "tag": verified.tag,
        "sequence": verified.sequence,
        "verified_at": datetime.now(UTC).isoformat(),
    }
    _ = (staging_dir / "provenance.json").write_text(
        json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _prepare_skill_dir(quarantine_root: Path, skill: str) -> Path:
    try:
        skill_dir = quarantine_root / skill
        skill_dir.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        quarantine_root.chmod(_PRIVATE_DIR_MODE)
        skill_dir.chmod(_PRIVATE_DIR_MODE)
    except OSError as error:
        raise QuarantineError(f"cannot prepare quarantine directory: {error}") from error
    return skill_dir


def stage_candidate(verified: VerifiedRelease, quarantine_root: Path) -> Path:
    """Stage a verified tree at ``quarantine/<skill>/<digest>/<skill>/`` atomically.

    Idempotent: an existing ``<digest>`` directory is returned untouched.
    Crash-safe: the candidate is assembled in a hidden temporary directory and
    published with one atomic ``os.replace`` as the very last step, so a crash
    mid-stage leaves no partial ``<digest>`` directory behind.
    """
    destination = quarantine_root / verified.skill / verified.digest
    if destination.is_dir():
        return destination
    _ensure_sane_tree(verified.tree_path)
    skill_dir = _prepare_skill_dir(quarantine_root, verified.skill)
    try:
        staging_dir = Path(tempfile.mkdtemp(dir=skill_dir, prefix=".stage-"))
    except OSError as error:
        raise QuarantineError(f"cannot create staging directory: {error}") from error
    try:
        _ = shutil.copytree(verified.tree_path, staging_dir / verified.skill, symlinks=False)
        _write_metadata(staging_dir, verified)
        for directory, _dirnames, _filenames in os.walk(staging_dir):
            Path(directory).chmod(_PRIVATE_DIR_MODE)
        os.replace(staging_dir, destination)
    except OSError as error:
        shutil.rmtree(staging_dir, ignore_errors=True)
        if destination.is_dir():
            return destination
        raise QuarantineError(f"cannot stage candidate: {error}") from error
    return destination
