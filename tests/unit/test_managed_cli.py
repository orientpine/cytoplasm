from __future__ import annotations

from pathlib import Path

import pytest

from automation.managed_sync import cli
from automation.managed_sync.pipeline import (
    FailedRelease,
    RemovalRequest,
    SkillOptions,
    SkippedSkill,
    StagedRelease,
    SyncConfig,
    SyncReport,
)
from automation.managed_sync.state import ManagedSyncState
from tests.unit.managed_cli_fixtures import (
    PRINCIPAL,
    config_payload,
    digest,
    install_config,
)


def test_sync_when_pipeline_reports_then_prints_deterministic_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    report = SyncReport(
        staged=(StagedRelease("managed-demo", 2, digest("b")),),
        skipped=(SkippedSkill("managed-idle", "not-opted-in"),),
        failed=(FailedRelease("managed-bad", "managed-bad/v1", "BAD-SIGNATURE"),),
        removal_requests=(
            RemovalRequest("managed-demo", digest("c"), "activated digest is revoked"),
        ),
        rolled_back=(StagedRelease("managed-demo", 1, digest("d")),),
    )
    seen: dict[str, SyncConfig] = {}

    def fake_sync_all(
        config: SyncConfig,
        state: ManagedSyncState,
        *,
        allow_rollback: int | None = None,
    ) -> SyncReport:
        del state, allow_rollback
        seen["config"] = config
        return report

    monkeypatch.setattr(cli, "sync_all", fake_sync_all)

    assert cli.main(["sync"]) == 0
    assert capsys.readouterr().out == (
        f"SYNC-STAGED skill=managed-demo sequence=2 digest={digest('b')}\n"
        f"SYNC-ROLLBACK-STAGED skill=managed-demo sequence=1 digest={digest('d')}\n"
        "SYNC-SKIPPED skill=managed-idle reason=not-opted-in\n"
        "SYNC-FAILED skill=managed-bad tag=managed-bad/v1 reason=BAD-SIGNATURE\n"
        f"SYNC-REMOVAL-REQUEST skill=managed-demo digest={digest('c')}"
        " reason=activated digest is revoked\n"
        "SYNC-SUMMARY staged=1 skipped=1 failed=1 removal_requests=1 rolled_back=1\n"
    )
    config = seen["config"]
    assert config.remote_url == "ssh://feed.example/managed-skills.git"
    assert config.state_path == tmp_path / "state.json"
    assert config.skills["managed-demo"] == SkillOptions(opt_in=True, pin=None)
    assert config.publisher_principal == PRINCIPAL


def test_sync_when_report_is_empty_then_prints_zero_summary_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))

    def empty_sync_all(
        config: SyncConfig,
        state: ManagedSyncState,
        *,
        allow_rollback: int | None = None,
    ) -> SyncReport:
        del config, state, allow_rollback
        return SyncReport((), (), ())

    monkeypatch.setattr(cli, "sync_all", empty_sync_all)

    assert cli.main(["sync"]) == 0
    assert capsys.readouterr().out == (
        "SYNC-SUMMARY staged=0 skipped=0 failed=0 removal_requests=0 rolled_back=0\n"
    )


def test_sync_when_allow_rollback_given_then_accepted_with_deterministic_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    seen: dict[str, int | None] = {}

    def fake_sync_all(
        config: SyncConfig,
        state: ManagedSyncState,
        *,
        allow_rollback: int | None = None,
    ) -> SyncReport:
        del config, state
        seen["allow_rollback"] = allow_rollback
        return SyncReport((), (), ())

    monkeypatch.setattr(cli, "sync_all", fake_sync_all)

    assert cli.main(["sync", "--allow-rollback", "3"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("SYNC-ROLLBACK-NOTE sequence=3 ")
    assert seen["allow_rollback"] == 3


def test_allow_rollback_when_given_to_other_subcommands_then_argparse_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    for argv in (
        ["status", "--allow-rollback", "3"],
        ["activate-instructions", "managed-demo", "--allow-rollback", "3"],
    ):
        with pytest.raises(SystemExit) as excinfo:
            _ = cli.main(argv)
        assert excinfo.value.code == 2
    _ = capsys.readouterr()
