"""DT-A5: obsidian source wiring in the ingest pipeline.

All tests inject a fake ``sync_mirror`` (no real git/network). Codified
decision 2: a mirror sync failure NEVER aborts the pipeline — WARN then scan
the last-good mirror if present, else skip the obsidian source for the run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_rag_ingest_pipeline import PERSPECTIVE, FakeMcpClient

from automation.rag_ingest import cli, statefile
from automation.rag_ingest import pipeline as pipeline_module
from automation.rag_ingest.config import IngestConfig, ObsidianSourceConfig
from automation.rag_ingest.pipeline import run_pipeline
from automation.rag_ingest.sources.obsidian import ObsidianSyncError, SyncResult

_RULES_YAML = "version: 1\ntags:\n  patent-sensitive:\n    keywords:\n      - 특허청구항\n"


def make_obsidian_config(tmp_path: Path) -> IngestConfig:
    for name in ("wiki", "notes", "notes/meetings"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    rules_path = tmp_path / "sensitivity-rules.yaml"
    _ = rules_path.write_text(_RULES_YAML, encoding="utf-8")
    obsidian = ObsidianSourceConfig(
        enabled=True,
        repo_url="ssh://git@git.example.invalid/vault.git",
        mirror_dir=tmp_path / "mirror",
        ssh_key_path=tmp_path / "id_ed25519",
        sensitivity_rules_path=rules_path,
    )
    return IngestConfig(
        mcp_base_url="http://fake:8765",
        api_key="test-key",
        state_dir=tmp_path / "state",
        wiki_dir=tmp_path / "wiki",
        notes_dir=tmp_path / "notes",
        meetings_dir=tmp_path / "notes" / "meetings",
        hermes_db=None,
        perspective=PERSPECTIVE,
        discord=None,
        obsidian=obsidian,
    )


def install_fake_sync(monkeypatch: pytest.MonkeyPatch) -> list[ObsidianSourceConfig]:
    """Replace pipeline's sync_mirror with a recording no-op fake."""
    calls: list[ObsidianSourceConfig] = []

    def fake_sync(config: ObsidianSourceConfig) -> SyncResult:
        calls.append(config)
        return SyncResult(action="fetched", mirror_dir=config.mirror_dir)

    monkeypatch.setattr(pipeline_module, "sync_mirror", fake_sync)
    return calls


def install_failing_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_sync(config: ObsidianSourceConfig) -> SyncResult:
        raise ObsidianSyncError("fake: git fetch refused")

    monkeypatch.setattr(pipeline_module, "sync_mirror", failing_sync)


def test_obsidian_note_is_synced_scanned_and_ingested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given — an enabled obsidian source with one mirror note and a fake sync
    config = make_obsidian_config(tmp_path)
    sync_calls = install_fake_sync(monkeypatch)
    assert config.obsidian is not None
    notes_dir = config.obsidian.mirror_dir / "10_projects"
    notes_dir.mkdir(parents=True)
    _ = (notes_dir / "a.md").write_text("옵시디언 프로젝트 노트", encoding="utf-8")
    client = FakeMcpClient()

    # When
    pending, log_lines = run_pipeline(config, {"obsidian"}, client=client)  # type: ignore[arg-type]

    # Then — synced once, note ingested with obsidian source_type metadata
    assert pending == 0
    assert sync_calls == [config.obsidian]
    (point,) = client.points.values()
    assert point["metadata"]["source_type"] == "obsidian"
    assert any("INGESTED obsidian:10_projects/a.md" in line for line in log_lines)


def test_sync_failure_scans_last_good_mirror_with_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given — sync fails but a HEALTHY last-good mirror clone exists on disk
    config = make_obsidian_config(tmp_path)
    install_failing_sync(monkeypatch)
    monkeypatch.setattr(pipeline_module, "mirror_is_healthy", lambda mirror_dir: True)
    assert config.obsidian is not None
    mirror_dir = config.obsidian.mirror_dir
    (mirror_dir / ".git").mkdir(parents=True)
    _ = (mirror_dir / "stale.md").write_text("지난 tick의 노트", encoding="utf-8")
    client = FakeMcpClient()

    # When
    pending, log_lines = run_pipeline(config, {"obsidian"}, client=client)  # type: ignore[arg-type]

    # Then — WARN logged, stale mirror still ingested, pipeline NOT aborted
    assert pending == 0
    assert any(line.startswith("WARN obsidian") for line in log_lines)
    assert any("INGESTED obsidian:stale.md" in line for line in log_lines)
    assert len(client.points) == 1


def test_sync_failure_with_unhealthy_mirror_skips_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given — sync fails and the mirror is a partial clone (HEAD unresolvable)
    config = make_obsidian_config(tmp_path)
    install_failing_sync(monkeypatch)
    monkeypatch.setattr(pipeline_module, "mirror_is_healthy", lambda mirror_dir: False)
    assert config.obsidian is not None
    mirror_dir = config.obsidian.mirror_dir
    (mirror_dir / ".git").mkdir(parents=True)
    _ = (mirror_dir / "stale.md").write_text("부분 클론의 잔재", encoding="utf-8")
    client = FakeMcpClient()

    # When
    pending, log_lines = run_pipeline(config, {"obsidian"}, client=client)  # type: ignore[arg-type]

    # Then — source skipped (nothing ingested), WARN names the unusable mirror
    assert pending == 0
    assert any("no usable mirror" in line for line in log_lines)
    assert len(client.points) == 0


def test_sync_failure_without_mirror_skips_source_but_run_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given — sync fails and no mirror was ever cloned; a wiki note coexists
    config = make_obsidian_config(tmp_path)
    install_failing_sync(monkeypatch)
    _ = (config.wiki_dir / "w.md").write_text("위키는 살아있다", encoding="utf-8")
    client = FakeMcpClient()

    # When
    pending, log_lines = run_pipeline(config, {"obsidian", "wiki"}, client=client)  # type: ignore[arg-type]

    # Then — obsidian skipped with WARN, other sources unaffected (no abort)
    assert pending == 0
    assert any(line.startswith("WARN obsidian") for line in log_lines)
    (point,) = client.points.values()
    assert point["metadata"]["source_type"] == "wiki"


def test_deleted_mirror_file_removes_its_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given — an ingested mirror note
    config = make_obsidian_config(tmp_path)
    _ = install_fake_sync(monkeypatch)
    assert config.obsidian is not None
    config.obsidian.mirror_dir.mkdir(parents=True)
    note = config.obsidian.mirror_dir / "gone.md"
    _ = note.write_text("삭제될 옵시디언 노트", encoding="utf-8")
    client = FakeMcpClient()
    _ = run_pipeline(config, {"obsidian"}, client=client)  # type: ignore[arg-type]
    assert len(client.points) == 1

    # When — the file disappears from the mirror (deleted in the vault)
    note.unlink()
    _ = run_pipeline(config, {"obsidian"}, client=client)  # type: ignore[arg-type]

    # Then — deletion sync removed its vectors and state entry
    assert len(client.points) == 0
    assert "obsidian:gone.md" not in statefile.load_state(config.state_path)["documents"]


def test_skipped_source_does_not_trigger_deletion_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given — an ingested mirror note, then the mirror becomes unavailable
    config = make_obsidian_config(tmp_path)
    _ = install_fake_sync(monkeypatch)
    assert config.obsidian is not None
    config.obsidian.mirror_dir.mkdir(parents=True)
    _ = (config.obsidian.mirror_dir / "keep.md").write_text("유지될 노트", encoding="utf-8")
    client = FakeMcpClient()
    _ = run_pipeline(config, {"obsidian"}, client=client)  # type: ignore[arg-type]
    assert len(client.points) == 1

    # When — sync fails AND the whole mirror directory is gone (skip path)
    install_failing_sync(monkeypatch)
    (config.obsidian.mirror_dir / "keep.md").unlink()
    config.obsidian.mirror_dir.rmdir()
    _ = run_pipeline(config, {"obsidian"}, client=client)  # type: ignore[arg-type]

    # Then — a skipped run must NOT wipe previously ingested vectors
    assert len(client.points) == 1
    assert "obsidian:keep.md" in statefile.load_state(config.state_path)["documents"]


def test_cli_accepts_obsidian_in_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Given — an explicit obsidian source and a missing config file
    missing_config = tmp_path / "absent.json"

    # When
    exit_code = cli.main(["run", "--sources", "obsidian", "--config", str(missing_config)])

    # Then — source name accepted; the failure is the missing config, not parsing
    captured = capsys.readouterr()
    assert "unknown sources" not in captured.err
    assert "FATAL" in captured.err
    assert exit_code == 1


def test_cli_default_sources_include_obsidian(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given — the cron watcher path: main(["run"]) with no --sources override
    config = make_obsidian_config(tmp_path)
    captured_sources: list[set[str]] = []

    def fake_run_pipeline(
        config: IngestConfig, sources: set[str], force: bool = False
    ) -> tuple[int, list[str]]:
        captured_sources.append(sources)
        return 0, []

    monkeypatch.setattr(cli, "load_config", lambda path: config)
    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    # When
    exit_code = cli.main(["run"])

    # Then — the default source set drives the obsidian branch every tick
    assert exit_code == 0
    assert captured_sources and "obsidian" in captured_sources[0]
