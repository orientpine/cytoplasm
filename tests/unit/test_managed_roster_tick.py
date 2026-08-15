from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.group_roster import RosterFetchConfig, RosterFetchError, RosterFetchResult
from automation.managed_sync import roster_tick
from automation.managed_sync.cron import managed_sync_watch as watch
from automation.managed_sync.fetch import ManagedFetchError


@dataclass(frozen=True, slots=True)
class _Config:
    mirror_dir: Path
    ssh_key_path: Path
    allowed_signers: Path
    publisher_principal: str


def _config(tmp_path: Path) -> _Config:
    return _Config(
        mirror_dir=tmp_path / "mirror",
        ssh_key_path=tmp_path / "key",
        allowed_signers=tmp_path / "allowed_signers",
        publisher_principal="publisher-testlab@autophagy",
    )


def _accept_fetch(_config: _Config) -> None:
    return None


def test_run_when_roster_changes_then_reports_updated_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the isolated roster refresher accepts a newly fetched branch tip.
    destination = tmp_path / "roster.yaml"
    seen: list[RosterFetchConfig] = []

    def fake_refresh(config: RosterFetchConfig) -> RosterFetchResult:
        seen.append(config)
        return RosterFetchResult(updated=True)

    monkeypatch.setattr(roster_tick, "refresh_roster", fake_refresh)
    monkeypatch.setattr(roster_tick, "sync_roster_ref", _accept_fetch)

    # When: the ordinary subscriber tick invokes the roster side path.
    roster_tick.run(_config(tmp_path), destination)

    # Then: the tick reports the replacement without entering a skill result type.
    assert capsys.readouterr().out == f"ROSTER-UPDATED path={destination}\n"
    assert seen == [
        RosterFetchConfig(
            mirror_dir=tmp_path / "mirror",
            roster_path=destination,
            allowed_signers=tmp_path / "allowed_signers",
            expected_principal="publisher-testlab@autophagy",
        )
    ]


def test_run_when_roster_is_rejected_then_reports_reason_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: roster verification rejects the fetched artifact.
    def reject(_config: RosterFetchConfig) -> RosterFetchResult:
        raise RosterFetchError("ROSTER-SIGNATURE", "detached signature verification failed")

    monkeypatch.setattr(roster_tick, "refresh_roster", reject)
    monkeypatch.setattr(roster_tick, "sync_roster_ref", _accept_fetch)

    # When: the roster side path runs beside a successful skill pipeline.
    roster_tick.run(_config(tmp_path), tmp_path / "roster.yaml")

    # Then: rejection is visible but remains outside skill failure/quarantine state.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ROSTER-REJECTED reason=ROSTER-SIGNATURE\n"


def test_run_when_roster_branch_fetch_fails_then_preserves_old_roster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: tag delivery succeeded, but the independent roster branch is absent.
    def reject_fetch(_config: _Config) -> None:
        raise ManagedFetchError("fatal: couldn't find remote ref refs/heads/roster")

    def unexpected_refresh(_config: RosterFetchConfig) -> RosterFetchResult:
        pytest.fail("a stale local roster ref must not be installed after fetch rejection")

    monkeypatch.setattr(roster_tick, "sync_roster_ref", reject_fetch, raising=False)
    monkeypatch.setattr(roster_tick, "refresh_roster", unexpected_refresh)

    # When: the independent roster side path runs.
    roster_tick.run(_config(tmp_path), tmp_path / "roster.yaml")

    # Then: rejection is visible and the old destination is untouched.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ROSTER-REJECTED reason=ROSTER-FETCH\n"


def test_watch_when_skill_sync_succeeds_then_runs_roster_side_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the shared tick owns its lease and the skill subprocess succeeds.
    events: list[str] = []
    monkeypatch.setattr(watch, "LEASE_ROOT", tmp_path / "leases")
    monkeypatch.setattr(watch, "SECRETS_PATH", tmp_path / "absent")
    monkeypatch.setattr(watch, "run_sync_once", lambda: (0, ""))
    monkeypatch.setattr(
        watch,
        "run_roster_once",
        lambda: events.append("roster"),
        raising=False,
    )

    # When: one natural cron/systemd tick runs.
    result = watch.run_tick()

    # Then: roster refresh runs in the tick wrapper after the skill path succeeds.
    assert result == 0
    assert events == ["roster"]


def test_watch_when_skill_sync_fails_then_does_not_use_stale_mirror_for_roster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the shared remote fetch or skill command fails before a current mirror is assured.
    events: list[str] = []
    monkeypatch.setattr(watch, "LEASE_ROOT", tmp_path / "leases")
    monkeypatch.setattr(watch, "SECRETS_PATH", tmp_path / "absent")
    monkeypatch.setattr(watch, "run_sync_once", lambda: (1, ""))
    monkeypatch.setattr(
        watch,
        "run_roster_once",
        lambda: events.append("roster"),
        raising=False,
    )

    # When: the failed natural tick returns.
    result = watch.run_tick()

    # Then: the roster side path does not install from a mirror whose fetch just failed.
    assert result == 1
    assert events == []
