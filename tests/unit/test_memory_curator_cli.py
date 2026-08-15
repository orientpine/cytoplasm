from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias

import pytest

from automation.memory_curator import cli
from automation.memory_curator.binding import PromotionReceipt
from automation.memory_curator.promotion import PromotionProposal
from automation.memory_curator.state import AlertState, CuratorState, PromotionRecord
from automation.memory_curator.state_store import save_state

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonLoader: TypeAlias = Callable[[str], JsonValue]
_JSON_LOADS: JsonLoader = json.loads


def _memory_dir(tmp_path: Path, user: str) -> Path:
    memories = tmp_path / "memories"
    memories.mkdir()
    _ = (memories / "MEMORY.md").write_text("", encoding="utf-8")
    _ = (memories / "USER.md").write_text(user, encoding="utf-8")
    return memories


def test_default_command_remains_a_non_mutating_compaction_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a non-canonical native file with duplicate entries.
    original = "same\n§\nsame"
    memories = _memory_dir(tmp_path, original)

    # When: the legacy default command is invoked without a verb.
    exit_code = cli.main(["--memory-dir", str(memories), "--kind", "user"])

    # Then: it reports the v1 compaction result without writing the file.
    payload = _JSON_LOADS(capsys.readouterr().out)
    assert exit_code == 0
    assert isinstance(payload, list) and isinstance(payload[0], dict)
    assert payload[0]["schema"] == "memory-curator-v1"
    freed_chars = payload[0]["freed_chars"]
    assert isinstance(freed_chars, int) and freed_chars > 0
    assert (memories / "USER.md").read_text(encoding="utf-8") == original


def test_reconcile_dry_run_calls_no_external_effect_and_writes_no_memory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a compactable durable entry and instrumented external effects.
    original = "  앞으로 배려를 원칙으로 한다  \n§\n앞으로 배려를 원칙으로 한다"
    memories = _memory_dir(tmp_path, original)
    state_path = tmp_path / "state.json"
    effects: list[str] = []

    def promote(_proposal: PromotionProposal) -> PromotionReceipt | None:
        effects.append("promote")
        return None

    def alert(_text: str) -> bool:
        effects.append("alert")
        return True

    def read(_slug: str) -> bytes | None:
        effects.append("read")
        return None

    monkeypatch.setattr(cli, "post_promotion", promote)
    monkeypatch.setattr(cli, "alert_owner", alert)
    monkeypatch.setattr(cli, "read_twin", read)
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)

    # When: the explicit reconcile dry-run command executes one cycle.
    exit_code = cli.main(
        [
            "reconcile",
            "--memory-dir",
            str(memories),
            "--state-path",
            str(state_path),
            "--dry-run",
        ]
    )

    # Then: intended work is reported while memory and external effects stay untouched.
    payload = _JSON_LOADS(capsys.readouterr().out)
    assert exit_code == 0
    assert isinstance(payload, dict)
    assert payload["dry_run"] is True
    compacted = payload["compacted"]
    assert isinstance(compacted, list) and isinstance(compacted[1], dict)
    assert compacted[1]["changed"] is True
    assert effects == []
    assert (memories / "USER.md").read_text(encoding="utf-8") == original
    assert "MEMORY_CURATOR_DRY_RUN" not in os.environ


def test_state_show_redacts_receipt_hash_and_private_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: persisted state containing receipt identifiers, hashes, and a private path.
    state_path = tmp_path / "state.json"
    record = PromotionRecord(
        source_kind="memory",
        entry_sha256="a" * 64,
        slug="memory-promoted-memory-sensitive",
        created_at="2026-07-30T12:00:00Z",
        note_sha256="b" * 64,
        draft_id="private-draft-id",
        confirm_message_id="private-message-id",
        status="reconciled",
        posted_at="2026-07-30T12:01:00Z",
        reconciled_at="2026-07-30T12:02:00Z",
        backup_path="/srv/autophagy-private/secret-backup",
        last_block_reason=None,
    )
    save_state(
        state_path,
        CuratorState(
            3,
            {"memory:" + "a" * 64: record},
            AlertState(None, None, None, None),
            {},
        ),
    )

    # When: the operator prints state through the redacted CLI view.
    exit_code = cli.main(["state", "show", "--state-path", str(state_path)])

    # Then: operational status remains visible but sensitive bindings do not.
    output = capsys.readouterr().out
    payload = _JSON_LOADS(output)
    assert exit_code == 0
    assert isinstance(payload, dict)
    promotions = payload["promotions"]
    assert isinstance(promotions, list) and isinstance(promotions[0], dict)
    assert promotions[0]["status"] == "reconciled"
    assert "private-draft-id" not in output
    assert "private-message-id" not in output
    assert "a" * 64 not in output and "b" * 64 not in output
    assert "/srv/autophagy-private" not in output
