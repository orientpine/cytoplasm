"""Disk-facing apply layer for the memory curator.

The only module that reads/writes the real MEMORY.md / USER.md.  Every
mutating write is preceded by a timestamped backup of the exact original
bytes and performed atomically (temp file + rename).  It applies ONLY the
autonomous, lossless compaction from :func:`curate`; durable entries stay
in place and are surfaced as promotion candidates for the owner-gated
twin flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .curator import curate, parse_memory_file, serialize_memory_file
from .model import CurationPlan, MemoryFile, MemoryKind

_FILENAMES: dict[MemoryKind, str] = {"memory": "MEMORY.md", "user": "USER.md"}


@dataclass(frozen=True, slots=True)
class CurationResult:
    kind: MemoryKind
    plan: CurationPlan
    changed: bool
    applied_path: Path
    backup_path: Path | None


def memory_path(memory_dir: Path, kind: MemoryKind) -> Path:
    if kind not in _FILENAMES:
        raise ValueError(f"unknown memory kind: {kind!r}")
    return memory_dir / _FILENAMES[kind]


def load_memory_file(memory_dir: Path, kind: MemoryKind) -> MemoryFile:
    path = memory_path(memory_dir, kind)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return parse_memory_file(text, kind=kind)


def apply_curation(
    memory_dir: Path,
    kind: MemoryKind,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
) -> CurationResult:
    path = memory_path(memory_dir, kind)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    plan = curate(parse_memory_file(original, kind=kind))
    canonical = serialize_memory_file(plan.compacted)
    changed = canonical != original

    if dry_run or not changed:
        return CurationResult(kind, plan, changed, path, None)

    stamp = (now or datetime.now(tz=timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    _ = backup.write_text(original, encoding="utf-8")
    tmp = path.with_name(f"{path.name}.curator-tmp")
    _ = tmp.write_text(canonical, encoding="utf-8")
    _ = tmp.replace(path)
    return CurationResult(kind, plan, changed, path, backup)
