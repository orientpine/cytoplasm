from __future__ import annotations

from pathlib import Path

import pytest

from automation.managed_sync import cli
from automation.managed_sync.state import (
    ManagedSyncState,
    load_state,
    record_activated,
    record_verified,
    save_state,
)
from tests.unit.managed_cli_fixtures import (
    config_payload,
    config_skills,
    digest,
    install_config,
    stage_release,
)


def test_status_when_state_and_quarantine_exist_then_renders_per_skill_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = config_payload(tmp_path)
    config_skills(payload)["managed-idle"] = {"opt_in": False, "pin": None}
    _ = install_config(tmp_path, monkeypatch, payload)
    state = record_verified(ManagedSyncState(), "managed-demo", 2, digest("b"))
    state = record_activated(state, "managed-demo", digest("a"))
    save_state(tmp_path / "state.json", state)
    quarantine_skill_dir = tmp_path / "quarantine" / "managed-demo"
    (quarantine_skill_dir / digest("a")).mkdir(parents=True)
    (quarantine_skill_dir / digest("b")).mkdir()

    assert cli.main(["status"]) == 0

    assert capsys.readouterr().out == (
        "STATUS skill=managed-demo opt_in=true highest_sequence=2"
        f" activated_digest={digest('a')} pending=1\n"
        "STATUS skill=managed-idle opt_in=false highest_sequence=0"
        " activated_digest=- pending=0\n"
    )


def test_mark_activated_when_live_mount_exists_then_records_link_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a mounted managed release while durable state still says nothing is active.
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    activated_digest = digest("a")
    release = tmp_path / "releases" / activated_digest
    release.mkdir(parents=True)
    live_root = tmp_path / "live"
    live_root.mkdir()
    (live_root / "managed-demo").symlink_to(release)

    # When: the operator reconciles bookkeeping from the authoritative live link.
    result = cli.main(["mark-activated", "managed-demo", "--live-root", str(live_root)])

    # Then: state records exactly the link-derived digest.
    assert result == 0
    assert load_state(tmp_path / "state.json").skill("managed-demo").activated_digest == activated_digest
    assert capsys.readouterr().out == (
        f"ACTIVATION-MARKED skill=managed-demo digest={activated_digest}\n"
    )


def test_mark_activated_when_live_mount_is_absent_then_clears_stale_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: durable state that still names a digest after the live mount was removed.
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    stale = record_activated(ManagedSyncState(), "managed-demo", digest("a"))
    save_state(tmp_path / "state.json", stale)
    live_root = tmp_path / "live"
    live_root.mkdir()

    # When: the operator reconciles bookkeeping from the authoritative absent link.
    result = cli.main(["mark-activated", "managed-demo", "--live-root", str(live_root)])

    # Then: the informational digest is cleared without inventing a replacement source of truth.
    assert result == 0
    assert load_state(tmp_path / "state.json").skill("managed-demo").activated_digest is None
    assert capsys.readouterr().out == "ACTIVATION-MARKED skill=managed-demo digest=-\n"


def test_activate_instructions_when_two_digests_quarantined_then_names_newest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    quarantine_dir = tmp_path / "quarantine"
    _ = stage_release(quarantine_dir, "managed-demo", 1, digest("a"))
    newest = stage_release(quarantine_dir, "managed-demo", 2, digest("b"))
    live_root = tmp_path / "live"
    live_root.mkdir()

    assert cli.main(["activate-instructions", "managed-demo", "--live-root", str(live_root)]) == 0
    assert capsys.readouterr().out == (
        f"automation/deploy-skill.sh managed-demo --activate-managed {newest}\n"
    )


def test_activate_instructions_when_live_base_exists_then_refuses_with_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    _ = stage_release(tmp_path / "quarantine", "managed-demo", 1, digest("a"))
    live_root = tmp_path / "live"
    (live_root / "demo").mkdir(parents=True)

    assert cli.main(["activate-instructions", "managed-demo", "--live-root", str(live_root)]) == 1

    error_output = capsys.readouterr().err
    assert "COLLISION-BLOCK" in error_output
    assert "live base demo" in error_output


def test_activate_instructions_when_nothing_quarantined_then_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    live_root = tmp_path / "live"
    live_root.mkdir()

    assert cli.main(["activate-instructions", "managed-demo", "--live-root", str(live_root)]) == 1
    assert "no quarantined release" in capsys.readouterr().err


def test_activate_instructions_when_name_lacks_managed_prefix_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))

    assert cli.main(["activate-instructions", "demo"]) == 2
    assert "must start with managed-" in capsys.readouterr().err
