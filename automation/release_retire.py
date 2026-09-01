"""Byte-exact retirement of an executed release approval."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from automation.interop.approval_lifecycle import Probe
from automation.release_spec import ReleaseSpecError


def archive_bytes(archive_root: Path, name: str, encoded: bytes) -> Path:
    """Move one approval record's exact bytes into a 0700 archive as a 0600 file.

    Single copy on purpose: the executed-release archive (``retire_released_record``)
    and the audited abandon (``release_abandon``) must write history identically —
    two archivers drift apart exactly where one of them stops being byte-exact.
    """
    archive_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive_root.chmod(0o700)
    target = archive_root / name
    if target.exists():
        if target.read_bytes() != encoded:
            raise ReleaseSpecError("release history conflicts with pending bytes")
        return target
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=archive_root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def retire_released_record(
    record_path: Path,
    archive_root: Path,
    *,
    expected_head: str,
    decision: Probe,
) -> Path | None:
    """Archive one executed release approval before the next request exists."""
    try:
        encoded = record_path.read_bytes()
        decoded = json.loads(encoded)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseSpecError("release record is unreadable") from error
    if not isinstance(decoded, dict):
        raise ReleaseSpecError("release record is not an object")
    head = str(decoded.get("head_sha", ""))
    if head != expected_head or len(head) != 40:
        raise ReleaseSpecError("pending release does not match the latest signed head")
    if decision is not Probe.APPROVED:
        raise ReleaseSpecError("only an approved release can leave pending")
    target = archive_bytes(archive_root, f"{head}.json", encoded)
    record_path.unlink()
    return target
