from __future__ import annotations

from pathlib import Path

import pytest

from automation.regression_bank.bank_state import BankStatus, main, read_state
from automation.regression_bank.weekly_bank import run_weekly_bank


def test_weekly_run_when_bank_passes_or_fails_then_records_state(tmp_path: Path) -> None:
    # Given: a deterministic bank runner and a private persistent state path.
    runner = tmp_path / "tests/e2e/run_bank.sh"
    runner.parent.mkdir(parents=True)
    state_path = tmp_path / "private/regression-bank/state.json"
    _ = runner.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    _ = runner.chmod(0o755)

    # When: the weekly runner observes a passing bank.
    pass_exit = run_weekly_bank(tmp_path, state_path)

    # Then: the green status is persisted for the apply gate.
    assert pass_exit == 0
    assert read_state(state_path).status is BankStatus.PASSING

    # When: the next weekly run observes a failing bank.
    _ = runner.write_text("#!/usr/bin/env bash\nexit 9\n", encoding="utf-8")
    fail_exit = run_weekly_bank(tmp_path, state_path)

    # Then: the red status replaces green state and preserves the actual failure code.
    assert fail_exit == 9
    state = read_state(state_path)
    assert state.status is BankStatus.FAILING
    assert state.returncode == 9


def test_record_command_when_given_failure_then_writes_state_and_reports_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a writable state path outside the production agent-owned directory.
    state_path = tmp_path / "private/regression-bank/state.json"

    # When: the remote runner records its real non-zero exit result through the CLI.
    exit_code = main(["record", "--returncode", "9", "--state-file", str(state_path)])

    # Then: the durable repair gate becomes red and the CLI emits its result line.
    output = capsys.readouterr().out
    assert exit_code == 0
    assert read_state(state_path).status is BankStatus.FAILING
    assert output.startswith("bank-state failing rc=9 at=")
