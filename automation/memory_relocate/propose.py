from __future__ import annotations

from datetime import datetime

from .binding import RelocationHashFields, relocation_action_hash
from .model import MemoryKind, RelocationError, RelocationRecord
from .plan import build_relocation_plan
from .rag_verify import rag_source_key


def build_proposed_record(
    entry_text: str,
    *,
    source_kind: MemoryKind,
    entry_sha256: str,
    reclaimable_chars: int,
    binding_kind: str,
    binding_surface: str,
    binding_channel_id: str,
    binding_policy_version: int,
    now: datetime,
) -> RelocationRecord:
    if source_kind != "memory":
        raise RelocationError("USER.md entries are never relocatable in v1")

    plan = build_relocation_plan(entry_text)
    note_relpath = plan.note_plan.relpath.as_posix()
    action_hash = relocation_action_hash(
        RelocationHashFields(source_kind, entry_sha256, note_relpath, plan.note_plan_sha256)
    )
    return RelocationRecord(
        version=1,
        source_kind=source_kind,
        entry_sha256=entry_sha256,
        note_relpath=note_relpath,
        note_plan_sha256=plan.note_plan_sha256,
        reclaimable_chars=reclaimable_chars,
        action_hash=action_hash,
        status="proposed",
        kind=binding_kind,
        surface=binding_surface,
        channel_id=binding_channel_id,
        policy_version=binding_policy_version,
        message_id=None,
        created_at=now.isoformat(),
        approved_at=None,
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=None,
        rag_source_key=rag_source_key(note_relpath),
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
    )
