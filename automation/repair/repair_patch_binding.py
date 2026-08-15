"""Bind a repair approval to the patch bytes it authorises, not to the file name.

The owner's ✅ has to mean "I saw this code change and agree". That requires two
things this module owns: an honest summary of what the patch touches, and an
action hash whose preimage contains both the raw bytes digest and that summary.
Anything this module cannot describe truthfully is refused rather than
under-reported — an omitted file would be an unapproved change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, TypeAlias

from automation.repair.repair_patch_diff import (
    PatchBindingError,
    assert_repo_relative,
    PatchFileDelta,
    parse_patch_changes,
)

BINDING_VERSION: Final = 2
OPERATION: Final = "repair.apply"
PATCH_FILE_NAME: Final = "patch.diff"
_DEV_NULL: Final = "/dev/null"
_HEX64: Final = frozenset("0123456789abcdef")
_OCTAL_DIGITS: Final = frozenset("01234567")
V2_KEYS: Final = ("content_binding_version", "patch_sha256", "changes", "patch_source_path")

ChangeJson: TypeAlias = dict[str, str | int | None]


__all__ = (
    "BINDING_VERSION",
    "ChangeJson",
    "ContentBinding",
    "PatchArtifact",
    "PatchBindingError",
    "PatchFileDelta",
    "changes_from_json",
    "changes_to_json",
    "content_action_hash",
    "decode_content_binding",
    "load_patch_artifact",
    "parse_patch_changes",
    "plan_patch_path",
)



ContentBinding: TypeAlias = tuple[int, str, tuple[PatchFileDelta, ...], str] | tuple[None, None, None, None]


@dataclass(frozen=True, slots=True)
class PatchArtifact:
    """Patch bytes captured once, with the digest and summary derived from them."""

    path: Path
    content: bytes = field(repr=False)
    patch_sha256: str
    changes: tuple[PatchFileDelta, ...]


def plan_patch_path(plan_root: Path, ticket_id: str) -> Path:
    """Return the one patch every planner writes and the approval gate re-reads."""
    return plan_root / ticket_id / PATCH_FILE_NAME


def load_patch_artifact(path: Path) -> PatchArtifact:
    """Read the patch once and derive everything the approval needs from those bytes."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise PatchBindingError("repair patch is unreadable") from error
    return PatchArtifact(path, content, hashlib.sha256(content).hexdigest(), parse_patch_changes(content))


def content_action_hash(
    ticket_id: str,
    patch_name: str,
    patch_sha256: str,
    changes: tuple[PatchFileDelta, ...],
) -> str:
    """Return the canonical binding over the bytes AND the summary the owner read."""
    preimage = {
        "binding_version": BINDING_VERSION,
        "changes": changes_to_json(changes),
        "operation": OPERATION,
        "patch_name": patch_name,
        "patch_sha256": patch_sha256,
        "ticket_id": ticket_id,
    }
    encoded = json.dumps(preimage, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def changes_to_json(changes: tuple[PatchFileDelta, ...]) -> list[ChangeJson]:
    """Project the summary onto the exact shape persisted and hashed."""
    return [
        {
            "deletions": change.deletions,
            "insertions": change.insertions,
            "new_path": change.new_path,
            "old_path": change.old_path,
        }
        for change in changes
    ]


def changes_from_json(raw: object) -> tuple[PatchFileDelta, ...]:
    """Rebuild a summary from storage, refusing anything the renderer could misreport."""
    if not isinstance(raw, list) or not raw:
        raise PatchBindingError("repair patch summary is missing or empty")
    return tuple(_change_from_json(entry) for entry in raw)


def decode_content_binding(decoded: dict[str, object]) -> ContentBinding:
    """Read the v2 keys as a set: all present, or all absent (a legacy record).

    A partial set is a malformed write, not an old record — accepting it would
    let a half-written summary authorise a patch nobody read.
    """
    present = tuple(key for key in V2_KEYS if key in decoded)
    if not present:
        return None, None, None, None
    if len(present) != len(V2_KEYS):
        raise PatchBindingError("repair approval content binding is incomplete")
    version = decoded["content_binding_version"]
    if type(version) is not int or version != BINDING_VERSION:
        raise PatchBindingError("repair approval content binding version is unsupported")
    digest = decoded["patch_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or not _HEX64.issuperset(digest):
        raise PatchBindingError("repair approval patch digest is invalid")
    source = decoded["patch_source_path"]
    if not isinstance(source, str) or not source:
        raise PatchBindingError("repair approval patch path is invalid")
    return version, digest, changes_from_json(decoded["changes"]), source


def _change_from_json(entry: object) -> PatchFileDelta:
    if not isinstance(entry, dict) or set(entry) != {"deletions", "insertions", "new_path", "old_path"}:
        raise PatchBindingError("repair patch summary entry has unexpected fields")
    old_path = _stored_path(entry["old_path"])
    new_path = _stored_path(entry["new_path"])
    if old_path is None and new_path is None:
        raise PatchBindingError("repair patch summary entry names no file")
    return PatchFileDelta(old_path, new_path, _count(entry["insertions"]), _count(entry["deletions"]))


def _stored_path(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PatchBindingError("repair patch summary path is not a string")
    assert_repo_relative(value)
    return value


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise PatchBindingError("repair patch summary line count is invalid")
    return value


