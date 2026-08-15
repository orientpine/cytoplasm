"""Fail-closed settlement of terminal memory-promotion approval surfaces."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

from .binding import parse_marker
from .state import CuratorState, PromotionRecord

SETTLED_SUFFIX_PREFIX: Final = "\n\n처리 완료 · memory-curator "
_TERMINAL: Final = frozenset({"reconciled", "abandoned"})

ProbeOutcome = Literal["bound", "missing", "binding-mismatch", "unverifiable"]
EditOutcome = Literal["edited", "already-settled", "missing", "binding-mismatch"]


class ClosureSurface(Protocol):
    def probe(self, channel_id: str, message_id: str, action_hash: str) -> ProbeOutcome: ...

    def edit_settled(
        self,
        channel_id: str,
        message_id: str,
        action_hash: str,
        suffix: str,
    ) -> EditOutcome: ...


@dataclass(frozen=True, slots=True)
class ClosureRequest:
    state: CuratorState
    gate_dir: Path
    surface: ClosureSurface
    dry_run: bool
    after_step: Callable[[str], None] = lambda _step: None


@dataclass(frozen=True, slots=True)
class UnboundItem:
    promotion_key: str
    message_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ClosureResult:
    close_lines: tuple[str, ...]
    unbound_items: tuple[UnboundItem, ...]
    orphans: tuple[str, ...]

    @property
    def closable(self) -> int:
        return len(self.close_lines)

    @property
    def unbound(self) -> int:
        return len(self.unbound_items)

    @property
    def lines(self) -> tuple[str, ...]:
        unbound = tuple(
            f"UNBOUND {item.promotion_key} {item.message_id} {item.reason}"
            for item in self.unbound_items
        )
        return self.close_lines + unbound + tuple(f"ORPHAN {name}" for name in self.orphans)


@dataclass(frozen=True, slots=True)
class _SavedDraft:
    path: Path
    raw: bytes
    record: Mapping[str, object]
    archived: bool


def _read_record(path: Path) -> tuple[bytes, Mapping[str, object]] | None:
    try:
        raw = path.read_bytes()
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return (raw, parsed) if isinstance(parsed, dict) else None


def _saved_draft(gate_dir: Path, draft_id: str) -> _SavedDraft | None:
    draft_path = gate_dir / "drafts" / f"{draft_id}.json"
    archive_path = gate_dir / "archive" / f"{draft_id}.json"
    for path, archived in ((draft_path, False), (archive_path, True)):
        loaded = _read_record(path)
        if loaded is not None:
            raw, record = loaded
            return _SavedDraft(path, raw, record, archived)
    return None


def _text(record: Mapping[str, object], key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) and value else None


def _binding_reason(key: str, state: PromotionRecord, saved: _SavedDraft) -> str | None:
    record = saved.record
    note_text = _text(record, "note_text")
    saved_hash = _text(record, "sha256")
    marker = parse_marker(note_text) if note_text is not None else None
    checks = (
        (state.draft_id == _text(record, "id"), "draft-id-mismatch"),
        (state.confirm_message_id == _text(record, "confirm_message_id"), "message-id-mismatch"),
        (marker is not None and marker.promotion_key == key, "promotion-key-mismatch"),
        (state.slug == _text(record, "slug"), "slug-mismatch"),
        (state.note_sha256 == saved_hash, "state-note-hash-mismatch"),
        (
            note_text is not None
            and saved_hash is not None
            and hashlib.sha256(note_text.encode("utf-8")).hexdigest() == saved_hash,
            "saved-note-hash-mismatch",
        ),
    )
    return next((reason for matches, reason in checks if not matches), None)


def _archive(gate_dir: Path, draft: _SavedDraft, draft_id: str) -> None:
    target = gate_dir / "archive" / f"{draft_id}.json"
    if target.is_file():
        if target.read_bytes() != draft.raw:
            raise RuntimeError(f"archive receipt mismatch: {draft_id}")
        return
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            _ = handle.write(draft.raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        temporary = None
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _drop_saved(draft: _SavedDraft) -> None:
    if draft.archived or not draft.path.exists():
        return
    if draft.path.read_bytes() != draft.raw:
        raise RuntimeError(f"saved draft changed before drop: {draft.path.name}")
    draft.path.unlink()


def _journal(gate_dir: Path, line: str) -> None:
    path = gate_dir / "curator-closure.jsonl"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(json.dumps({"event": line}, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _memory_orphans(
    gate_dir: Path,
    promotions: Mapping[str, PromotionRecord],
) -> tuple[str, ...]:
    known = {record.draft_id for record in promotions.values() if record.draft_id}
    drafts = gate_dir / "drafts"
    if not drafts.is_dir():
        return ()
    orphans: list[str] = []
    for path in sorted(drafts.glob("*.json")):
        loaded = _read_record(path)
        if loaded is None:
            continue
        _, record = loaded
        draft_id = _text(record, "id")
        note_text = _text(record, "note_text")
        marker = parse_marker(note_text) if note_text is not None else None
        if record.get("status") == "saved" and marker is not None and draft_id not in known:
            orphans.append(path.name)
    return tuple(orphans)


def close_terminal_promotions(request: ClosureRequest) -> ClosureResult:
    close_lines: list[str] = []
    unbound: list[UnboundItem] = []
    for key, state_record in sorted(request.state.promotions.items()):
        if state_record.status not in _TERMINAL:
            continue
        message_id = state_record.confirm_message_id or "-"
        draft_id = state_record.draft_id
        saved = _saved_draft(request.gate_dir, draft_id) if draft_id is not None else None
        if saved is None:
            unbound.append(UnboundItem(key, message_id, "saved-draft-missing"))
            continue
        reason = _binding_reason(key, state_record, saved)
        if reason is not None:
            unbound.append(UnboundItem(key, message_id, reason))
            continue
        channel_id = _text(saved.record, "channel_id")
        action_hash = _text(saved.record, "sha256")
        if channel_id is None or action_hash is None or message_id == "-":
            unbound.append(UnboundItem(key, message_id, "surface-binding-missing"))
            continue
        probe = request.surface.probe(channel_id, message_id, action_hash)
        if probe != "bound":
            unbound.append(UnboundItem(key, message_id, f"remote-{probe}"))
            continue
        close_line = f"CLOSE {key} {channel_id} {message_id}"
        close_lines.append(close_line)
        if request.dry_run:
            continue
        suffix = f"{SETTLED_SUFFIX_PREFIX}{key}"
        edited = request.surface.edit_settled(channel_id, message_id, action_hash, suffix)
        if edited == "binding-mismatch":
            close_lines.pop()
            unbound.append(UnboundItem(key, message_id, "remote-binding-mismatch"))
            continue
        request.after_step("after-patch")
        _archive(request.gate_dir, saved, draft_id)
        request.after_step("after-archive")
        request.after_step("before-drop")
        _drop_saved(saved)
        _journal(request.gate_dir, close_line)
    orphans = _memory_orphans(request.gate_dir, request.state.promotions)
    if not request.dry_run:
        for item in unbound:
            _journal(request.gate_dir, f"UNBOUND {item.promotion_key} {item.message_id} {item.reason}")
        for orphan in orphans:
            _journal(request.gate_dir, f"ORPHAN {orphan}")
    return ClosureResult(tuple(close_lines), tuple(unbound), orphans)
