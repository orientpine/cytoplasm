from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

RELOCATION_HASH_VERSION = "mc-reloc-v1"


@dataclass(frozen=True, slots=True)
class RelocationHashFields:
    source_kind: str
    entry_sha256: str
    note_relpath: str
    note_plan_sha256: str


def relocation_action_hash(fields: RelocationHashFields) -> str:
    encoded = json.dumps(
        {
            "delete_intent": True,
            "destination_kind": "obsidian",
            "note_plan_sha256": fields.note_plan_sha256,
            "note_relpath": fields.note_relpath,
            "source_entry_sha256": fields.entry_sha256,
            "source_kind": fields.source_kind,
            "version": RELOCATION_HASH_VERSION,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
