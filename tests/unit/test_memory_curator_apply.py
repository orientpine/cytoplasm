"""Contract for applying curation to real memory files (with backup).

The apply layer is the only part that touches disk.  It must be:

* **reversible** — every mutating write is preceded by a timestamped backup
  of the exact original bytes;
* **atomic** — the new content is written to a temp file and renamed;
* **idempotent** — a second run over an already-canonical file is a no-op
  (no write, no backup);
* **lossless / owner-gated** — it only applies the autonomous compaction;
  durable entries are preserved in place and merely surfaced as promotion
  candidates on the returned plan.
"""

from __future__ import annotations

from pathlib import Path

from automation.memory_curator.apply import (
    CurationResult,
    apply_curation,
    load_memory_file,
    memory_path,
)


def test_memory_path_maps_kind_to_filename(tmp_path: Path) -> None:
    assert memory_path(tmp_path, "memory").name == "MEMORY.md"
    assert memory_path(tmp_path, "user").name == "USER.md"


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_memory_file(tmp_path, "memory").entries == ()


def test_dry_run_reports_change_but_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    original = "A\n§\nA\n§\nB"
    path.write_text(original, encoding="utf-8")
    result = apply_curation(tmp_path, "memory", dry_run=True)
    assert isinstance(result, CurationResult)
    assert result.changed is True
    assert result.backup_path is None
    assert path.read_text(encoding="utf-8") == original  # untouched


def test_apply_dedupes_and_backs_up_original(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    original = "A\n§\nA\n§\nB"
    path.write_text(original, encoding="utf-8")
    result = apply_curation(tmp_path, "memory", dry_run=False)
    assert result.changed is True
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.backup_path.read_text(encoding="utf-8") == original
    assert path.read_text(encoding="utf-8") == "A\n§\nB"


def test_apply_is_idempotent_no_write_no_backup(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    path.write_text("A\n§\nB", encoding="utf-8")
    result = apply_curation(tmp_path, "memory", dry_run=False)
    assert result.changed is False
    assert result.backup_path is None
    # a second pass over the canonical form is also a no-op
    again = apply_curation(tmp_path, "memory", dry_run=False)
    assert again.changed is False


def test_apply_preserves_durable_entries_and_flags_them(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    path.write_text(
        "이름은 <owner-name>\n§\n앞으로 배려를 원칙으로 한다\n§\n이름은 <owner-name>",
        encoding="utf-8",
    )
    result = apply_curation(tmp_path, "user", dry_run=False)
    text = path.read_text(encoding="utf-8")
    assert "원칙" in text  # durable entry never auto-removed
    assert text.count("이름은 <owner-name>") == 1  # exact duplicate deduped
    assert any("원칙" in e.text for e in result.plan.promotion_candidates)


def test_backup_filename_is_timestamped_and_distinct(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    path.write_text("A\n§\nA", encoding="utf-8")
    result = apply_curation(tmp_path, "memory", dry_run=False)
    assert result.backup_path is not None
    assert result.backup_path.name.startswith("MEMORY.md.bak-")
    assert result.backup_path != path
