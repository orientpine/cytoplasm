from __future__ import annotations

from pathlib import Path

from automation import supply_chain_watch_cli
from automation.supply_chain_records import enumerate_pending
from automation.supply_chain_watch import FailureAttempt
from automation.supply_chain_watch_cli import load_failures, write_tick_summary


class _Directory:
    def skill_approvals(self) -> str:
        return "channel"


class _Identity:
    def directory(self) -> _Directory:
        return _Directory()


def _seed_failure(path: Path) -> dict[str, FailureAttempt]:
    failures = {"skill-deploy:stale": FailureAttempt("release-a:resume-exit:5", 2, 3700.0)}
    write_tick_summary(
        path,
        (),
        release_sha="release-a",
        timestamp="2026-08-05T00:00:00Z",
        failures=failures,
    )
    return failures


def _wire_main(monkeypatch, *, gate_dir: Path, state_path: Path) -> None:
    monkeypatch.setenv("SUPPLY_CHAIN_WATCH_STATE", str(state_path))
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "GATE_DIR", gate_dir)
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_identity", lambda: _Identity())
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_owner_id", lambda: "owner")


def test_successful_empty_enumeration_sweeps_all_stale_failures(
    tmp_path: Path, monkeypatch
) -> None:
    # Given: a stale suppression record and a successfully readable empty pending directory.
    gate_dir = tmp_path / "gate"
    (gate_dir / "pending").mkdir(parents=True)
    state_path = tmp_path / "tick.json"
    _ = _seed_failure(state_path)
    _wire_main(monkeypatch, gate_dir=gate_dir, state_path=state_path)

    # When: the CLI creates the next tick.
    exit_code = supply_chain_watch_cli.main()

    # Then: success plus requests=() proves every old key is stale.
    assert exit_code == 0
    assert load_failures(state_path) == {}


def test_failed_enumeration_preserves_all_failures(tmp_path: Path, monkeypatch) -> None:
    # Given: a stale suppression record and an OSError while listing pending records.
    gate_dir = tmp_path / "gate"
    state_path = tmp_path / "tick.json"
    failures = _seed_failure(state_path)
    _wire_main(monkeypatch, gate_dir=gate_dir, state_path=state_path)

    def raise_os_error(_path: Path, _pattern: str):
        raise OSError("enumeration unavailable")

    monkeypatch.setattr(Path, "glob", raise_os_error)

    # When: the explicit enumeration contract and the CLI observe the failure.
    enumeration = enumerate_pending(gate_dir)
    exit_code = supply_chain_watch_cli.main()

    # Then: succeeded=False means pop zero keys, regardless of requests=().
    assert enumeration.succeeded is False
    assert enumeration.requests == ()
    assert exit_code == 0
    assert load_failures(state_path) == failures
