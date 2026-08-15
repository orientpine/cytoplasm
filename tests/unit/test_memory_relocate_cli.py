from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias

import pytest

from automation.memory_curator.binding import entry_digest
from automation.memory_relocate.cli import main
from automation.memory_relocate.model import RelocationRecord, RelocationState, record_key
from automation.memory_relocate.store import load_state, save_state


_ENTRY = "<primary-node>는 prod이고 <rag-node>는 개인 RAG 전용이다."
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonLoader: TypeAlias = Callable[[str], JsonValue]
_JSON_LOADS: JsonLoader = json.loads


def _json_object(document: str) -> dict[str, JsonValue]:
    parsed = _JSON_LOADS(document)
    assert isinstance(parsed, dict)
    return parsed


def _record() -> RelocationRecord:
    return RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256="a" * 64,
        note_relpath="000_PARA/Resource/ops-reference.md",
        note_plan_sha256="b" * 64,
        reclaimable_chars=321,
        action_hash=f"sha256:{'c' * 64}",
        status="proposed",
        kind="obsidian-write",
        surface="owner-dm",
        channel_id="123456789",
        policy_version=6,
        message_id="private-message-id",
        created_at="2026-07-31T10:00:00+00:00",
        approved_at=None,
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=None,
        rag_source_key="obsidian:000_PARA/Resource/ops-reference.md",
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
    )


def test_state_show_when_relocations_exist_redacts_bindings_and_reports_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: persisted relocation state containing a message binding and an unpersisted entry.
    record = _record()
    state_path = tmp_path / "private" / "relocations.json"
    save_state(
        state_path,
        RelocationState(
            version=1,
            relocations={record_key(record.source_kind, record.entry_sha256): record},
        ),
    )

    # When: the owner asks for the redacted state view.
    exit_code = main(["state", "show", "--state-path", str(state_path)])

    # Then: operational facts remain visible without exposing raw text or message identifiers.
    output = capsys.readouterr().out
    payload = _json_object(output)
    status_counts = payload["status_counts"]
    relocations = payload["relocations"]
    assert exit_code == 0
    assert _ENTRY not in output
    assert "private-message-id" not in output
    assert status_counts == {"proposed": 1}
    assert isinstance(relocations, list)
    relocation = relocations[0]
    assert isinstance(relocation, dict)
    assert relocation["note_relpath"] == record.note_relpath
    assert relocation["reclaimable_chars"] == record.reclaimable_chars
    assert relocation["digest8"] == "a" * 8


def test_propose_when_dry_run_does_not_persist_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: one OPS_REFERENCE entry and an absent relocation state file.
    entry_path = tmp_path / "entry.md"
    state_path = tmp_path / "private" / "relocations.json"
    _ = entry_path.write_text(_ENTRY, encoding="utf-8")

    # When: the owner previews a proposal.
    exit_code = main(
        [
            "propose",
            "--entry-file",
            str(entry_path),
            "--state-path",
            str(state_path),
            "--channel-id",
            "123456789",
            "--dry-run",
        ]
    )

    # Then: it prints a JSON proposal but creates no persistent state.
    payload = _json_object(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["dry_run"] is True
    assert payload["status"] == "proposed"
    assert not state_path.exists()


def test_propose_when_not_dry_run_persists_proposed_record(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: one OPS_REFERENCE entry and a state path whose parent is absent.
    entry_path = tmp_path / "entry.md"
    state_path = tmp_path / "private" / "relocations.json"
    _ = entry_path.write_text(_ENTRY, encoding="utf-8")

    # When: the owner persists the proposal.
    exit_code = main(
        [
            "propose",
            "--entry-file",
            str(entry_path),
            "--state-path",
            str(state_path),
            "--channel-id",
            "123456789",
        ]
    )

    # Then: the saved state contains the exact source-qualified proposed record.
    payload = _json_object(capsys.readouterr().out)
    digest = entry_digest("memory", _ENTRY)
    record = load_state(state_path).relocations[record_key("memory", digest)]
    assert exit_code == 0
    assert payload["dry_run"] is False
    assert record.status == "proposed"
    assert record.message_id is None


def test_propose_when_entry_file_is_missing_refuses_on_one_stderr_line(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an entry-file path that does not exist.
    # When: the owner tries to propose it.
    exit_code = main(
        [
            "propose",
            "--entry-file",
            str(tmp_path / "missing.md"),
            "--state-path",
            str(tmp_path / "relocations.json"),
        ]
    )

    # Then: the CLI fails closed without a traceback or multi-line usage dump.
    error = capsys.readouterr().err
    assert exit_code == 2
    assert len(error.splitlines()) == 1
    assert "Traceback" not in error
