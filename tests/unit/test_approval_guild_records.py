"""Persisted coordinates survive real codecs without entering approval hashes."""
from __future__ import annotations

import json
from dataclasses import fields, replace
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

import pytest

from automation.plaud_sync import model as plaud
from automation.memory_relocate import model as memory
from automation.repair.repair_ops_approval import repair_action_hash
from automation.repair.repair_ops_pending import PendingRepairApproval, PendingRepairApprovalStore
from tests.unit.test_plaud_sync_model import _BASE
from tests.unit.test_memory_relocate_model import _record as memory_record
from tests.unit.test_calendar_coordination_approval_characterization import _coordination_entry
from tests.unit.test_wiki_patent_approval_characterization import _manifest
from tests.unit.test_todo_approval_store import _spec

coordination_pending = import_module("coordination_pending")
todo_approval_store = import_module("todo_approval_store")
todo_approval_store_io = import_module("todo_approval_store_io")
pm = import_module("scripts.patent_export_manifest")
semantic_action_hash = import_module("scripts.patent_export_approval").semantic_action_hash


@pytest.mark.parametrize("producer", ["plaud", "memory", "coordination", "patent", "repair", "todo"])
@pytest.mark.parametrize("guild", [None, "111"])
def test_guild_record_roundtrip_when_metadata_is_optional(producer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guild: str | None) -> None:
    # Given: records created before guild metadata existed.
    monkeypatch.setenv("PATENT_EXPORT_ROOT", str(tmp_path))
    # When: each real codec or store round-trips its own record.
    match producer:
        case "plaud":
            before = _BASE
            if guild is not None:
                assert "approval_guild_id" in {field.name for field in fields(before)}
                before = replace(before, approval_guild_id=guild)
            after = plaud.parse_record(json.loads(json.dumps(plaud.serialize_record(before))))
        case "memory":
            before = memory_record()
            if guild is not None:
                assert "approval_guild_id" in {field.name for field in fields(before)}
                before = replace(before, approval_guild_id=guild)
            after = memory._parse_record(json.loads(json.dumps(memory._serialize_record(before))))
        case "coordination":
            before = _coordination_entry()
            if guild is not None:
                assert "approval_guild_id" in {field.name for field in fields(before)}
                before = replace(before, approval_guild_id=guild)
            after = coordination_pending._parse_entry(json.loads(before.as_json()))
        case "patent":
            before = _manifest("synthetic")
            if guild is not None:
                assert "approval_guild_id" in {field.name for field in fields(before)}
                before = replace(before, approval_guild_id=guild)
            pm.write_manifest(before)
            if guild is None:
                path = pm.manifest_path(before.slug)
                raw = json.loads(path.read_text())
                raw.pop("approval_guild_id", None)
                path.write_text(json.dumps(raw))
            after = pm.load_manifest(before.slug)
        case "repair":
            before = PendingRepairApproval("synthetic", "patch.diff", repair_action_hash("synthetic", "patch.diff"), "nonce", "444", datetime(2026, 9, 1, tzinfo=UTC))
            if guild is not None:
                assert "approval_guild_id" in {field.name for field in fields(before)}
                before = replace(before, approval_guild_id=guild)
            store = PendingRepairApprovalStore(tmp_path)
            store.save(before)
            if guild is None:
                path = store._path(before.ticket_id)
                raw = json.loads(path.read_text())
                raw.pop("approval_guild_id", None)
                path.write_text(json.dumps(raw))
            after = store.get(before.ticket_id)
        case "todo":
            store = todo_approval_store.TodoApprovalStore(tmp_path)
            before = store.prepare(_spec(todo_approval_store), datetime(2026, 9, 1, tzinfo=UTC))
            if guild is not None:
                assert "approval_guild_id" in {field.name for field in fields(before)}
                before = replace(before, approval_guild_id=guild)
            raw = json.loads(todo_approval_store_io._encoded(before))
            if guild is None:
                raw.pop("approval_guild_id", None)
            after = todo_approval_store_io.decode(json.dumps(raw))
        case _:
            raise AssertionError(producer)
    # Then: legacy metadata is not invented and authorizing fields remain identical.
    assert after == before
    assert getattr(after, "approval_guild_id", None) == guild
    if guild is not None:
        legacy = replace(before, approval_guild_id=None)
        if producer == "patent":
            assert semantic_action_hash(before) == semantic_action_hash(legacy)
        else:
            assert getattr(after, "action_hash", getattr(after, "sha256", None)) == getattr(legacy, "action_hash", getattr(legacy, "sha256", None))
