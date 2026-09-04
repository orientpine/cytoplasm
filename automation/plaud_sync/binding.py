"""Composite action hash binding one recording to its exact note payload."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

PLAUD_SYNC_HASH_VERSION: Final = "plaud-sync-v1"


@dataclass(frozen=True, slots=True)
class PlaudHashFields:
    recording_id: str
    note_relpath: str
    note_title: str
    body_sha256: str


def plaud_action_hash(fields: PlaudHashFields) -> str:
    encoded = json.dumps(
        {
            "body_sha256": fields.body_sha256,
            "destination_kind": "obsidian",
            "note_relpath": fields.note_relpath,
            "note_title": fields.note_title,
            "recording_id": fields.recording_id,
            "version": PLAUD_SYNC_HASH_VERSION,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
