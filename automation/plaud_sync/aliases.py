"""Explicit, binding-preserving migration of folder-move recording aliases."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from .model import PlaudStatus, PlaudSyncRecord, PlaudSyncState, canonical_recording_id
from .store import load_state, save_state

# Lower rank wins. Equal progress uses the state key for an input-order-independent tie.
_ORDER: Final[tuple[PlaudStatus, ...]] = (
    "written", "approved", "posted", "planned", "transcribing", "abandoned",
)


@dataclass(frozen=True, slots=True)
class AliasMerge:
    kept_id: str
    removed_id: str
    kept_status: PlaudStatus
    removed_status: PlaudStatus


def migrate_aliases(state: PlaudSyncState) -> tuple[PlaudSyncState, tuple[AliasMerge, ...]]:
    """Keep the most advanced record whole; never rebind hashes, cards or note paths."""
    groups: dict[str, list[str]] = {}
    for key in state.records:
        groups.setdefault(canonical_recording_id(key), []).append(key)
    records = dict(state.records)
    report: list[AliasMerge] = []
    for keys in groups.values():
        if len(keys) < 2:
            continue
        ordered = sorted(keys, key=lambda key: (_ORDER.index(records[key].status), key))
        winner_key = ordered[0]
        winner: PlaudSyncRecord = records[winner_key]
        aliases = list(winner.aliases)
        for key in ordered[1:]:
            loser = records.pop(key)
            for alias in (key, loser.recording_id, *loser.aliases):
                if alias != winner.recording_id and alias not in aliases:
                    aliases.append(alias)
            report.append(AliasMerge(winner_key, key, winner.status, loser.status))
        records[winner_key] = replace(winner, aliases=tuple(aliases))
    return replace(state, records=records), tuple(report)


def migrate_file(path: Path, *, apply: bool) -> tuple[str, ...]:
    """Caller holds watch.lock; back up exact bytes before the atomic state replacement."""
    state, report = migrate_aliases(load_state(path))
    lines = tuple(
        f"plaud-sync: alias keep={entry.kept_id} status={entry.kept_status} "
        f"merge={entry.removed_id} status={entry.removed_status}"
        for entry in report
    )
    if not apply or not report:
        return lines + (f"plaud-sync: aliases mode=dry-run merges={len(report)}" if not apply
                        else "plaud-sync: aliases mode=apply merges=0",)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    original = path.read_bytes()
    with backup.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    save_state(path, state)
    return lines + (f"plaud-sync: aliases mode=apply merges={len(report)} backup={backup}",)
