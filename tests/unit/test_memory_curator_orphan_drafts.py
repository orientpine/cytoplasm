from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automation.memory_curator.binding import (
    DeletionMarker,
    MARKER_VERSION,
    promotion_key,
    promoted_slug,
    render_marker,
)
from automation.memory_curator.closure import ClosureRequest, close_terminal_promotions
from automation.memory_curator.closure_cli import main
from automation.memory_curator.state import CuratorState, PromotionRecord, serialize_state

from test_memory_curator_closure import _fixture


def _saved_memory_draft(gate: Path, draft_id: str, digest: str) -> None:
    key = promotion_key("memory", digest)
    note = render_marker(DeletionMarker(MARKER_VERSION, key, "memory", digest, True))
    note_hash = hashlib.sha256(note.encode()).hexdigest()
    record = {
        "action": "create",
        "channel_id": "orphan-channel",
        "confirm_message_id": f"message-{draft_id}",
        "created": "2026-08-05T00:00:00Z",
        "id": draft_id,
        "note_text": note,
        "sha256": note_hash,
        "slug": promoted_slug("memory", digest),
        "status": "saved",
    }
    (gate / "drafts" / f"{draft_id}.json").write_text(json.dumps(record) + "\n", encoding="utf-8")


def test_orphan_memory_draft_is_listed_without_outbox_or_mutation(tmp_path: Path) -> None:
    state, gate, surface, _key, _saved = _fixture(tmp_path)
    _saved_memory_draft(gate, "orphan-1", "d" * 64)
    before = {path: path.read_bytes() for path in gate.rglob("*") if path.is_file()}

    result = close_terminal_promotions(ClosureRequest(state, gate, surface, True))

    assert result.orphans == ("orphan-1.json",)
    assert before == {path: path.read_bytes() for path in gate.rglob("*") if path.is_file()}
    assert state.pending_owner_events == {}


def test_non_memory_saved_draft_is_untouched(tmp_path: Path) -> None:
    state, gate, surface, _key, _saved = _fixture(tmp_path)
    ordinary = gate / "drafts" / "ordinary.json"
    ordinary.write_text(json.dumps({"id": "ordinary", "status": "saved", "note_text": "plain"}) + "\n", encoding="utf-8")
    result = close_terminal_promotions(ClosureRequest(state, gate, surface, True))
    assert result.orphans == ()
    assert ordinary.is_file()


def test_dry_run_cli_exact_listing_and_summary_is_non_mutating(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    state, gate, surface, key, saved = _fixture(tmp_path)
    second_key = promotion_key("memory", "b" * 64)
    second_note = render_marker(DeletionMarker(MARKER_VERSION, second_key, "memory", "b" * 64, True))
    second_hash = hashlib.sha256(second_note.encode()).hexdigest()
    second_saved = {**saved, "id": "draft-2", "confirm_message_id": "message-2", "note_text": second_note, "sha256": second_hash, "slug": promoted_slug("memory", "b" * 64)}
    (gate / "drafts" / "draft-2.json").write_text(json.dumps(second_saved) + "\n", encoding="utf-8")
    base_record = state.promotions[key]
    second_record = PromotionRecord("memory", "b" * 64, str(second_saved["slug"]), base_record.created_at, second_hash, "draft-2", "message-2", "reconciled", base_record.posted_at, base_record.reconciled_at, None, None)
    missing_record = PromotionRecord("memory", "c" * 64, promoted_slug("memory", "c" * 64), base_record.created_at, "f" * 64, "gone", "message-gone", "abandoned", base_record.posted_at, base_record.reconciled_at, None, None)
    mismatch_record = replace_record(base_record, draft_id="draft-mismatch", confirm_message_id="message-state")
    mismatch_saved = {**saved, "id": "draft-mismatch", "confirm_message_id": "message-saved"}
    (gate / "drafts" / "draft-mismatch.json").write_text(json.dumps(mismatch_saved) + "\n", encoding="utf-8")
    _saved_memory_draft(gate, "orphan-1", "d" * 64)
    all_state = CuratorState(state.version, {key: base_record, second_key: second_record, "memory:" + "c" * 64: missing_record, "mismatch": mismatch_record}, state.alert, {})
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(serialize_state(all_state), sort_keys=True) + "\n", encoding="utf-8")
    surface.contents["message-2"] = f"sha256:{second_hash}"
    monkeypatch.setenv("MEMORY_CURATOR_STATE", str(state_path))
    monkeypatch.setenv("WIKI_GATE_DIR", str(gate))
    monkeypatch.setattr("automation.memory_curator.closure_cli.build_surface", lambda: surface)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    assert main(["--dry-run"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "CLOSURE-DRYRUN closable=2 unbound=2 orphans=1"
    assert sum(line.startswith("CLOSE ") for line in lines) == 2
    assert sum(line.startswith("UNBOUND ") for line in lines) == 2
    assert lines.count("ORPHAN orphan-1.json") == 1
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def replace_record(record: PromotionRecord, **changes: str) -> PromotionRecord:
    values = {name: getattr(record, name) for name in record.__dataclass_fields__}
    values.update(changes)
    return PromotionRecord(**values)
