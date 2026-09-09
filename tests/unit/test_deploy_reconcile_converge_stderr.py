"""Converger stderr regressions split out because the original reconciler tests are FS3-pinned."""
from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest

from automation.deploy_reconcile_cli import converge_command, run_converge

_MARKER = "[deploy-reconcile] HELPER-FAILED converge: "


@pytest.mark.parametrize("returncode", [1, 4, 6])
def test_failure_reemits_stderr_and_preserves_exit_code(
    returncode: int, capsys: pytest.CaptureFixture[str],
) -> None:
    command = (
        sys.executable, "-c",
        "import sys; print('stdout-not-for-journal'); "
        "sys.stderr.write('  CONVERGE-STDERR-PROBE\\n\\tverifier missing  \\n'); "
        f"sys.exit({returncode})",
    )

    assert run_converge(command) == returncode

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _MARKER + "CONVERGE-STDERR-PROBE verifier missing\n"


@pytest.mark.parametrize("length", [399, 400, 401, 1200])
def test_failure_collapses_whitespace_before_clipping_stderr(
    length: int, capsys: pytest.CaptureFixture[str],
) -> None:
    command = (
        sys.executable, "-c", "import sys; "
        f"sys.stderr.write(' \\t\\n' * 200 + 'x' * {length} + '\\n'); sys.exit(4)",
    )

    assert run_converge(command) == 4

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _MARKER + "x" * min(length, 400) + "\n"


@pytest.mark.parametrize("returncode", [0, 5])
def test_success_and_lock_contention_are_silent_even_with_stderr(
    returncode: int, capsys: pytest.CaptureFixture[str],
) -> None:
    command = (
        sys.executable, "-c", "import sys; print('stdout-not-for-journal'); "
        f"sys.stderr.write('CONVERGE-STDERR-PROBE\\n'); sys.exit({returncode})",
    )

    assert run_converge(command) == returncode

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("error", [
    OSError("cannot execute"),
    subprocess.SubprocessError("subprocess failed"),
    subprocess.TimeoutExpired("converge", 900),
])
def test_execution_errors_remain_exit_one_and_emit_failure_marker(
    error: Exception, capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("automation.deploy_reconcile_cli.subprocess.run", side_effect=error):
        assert run_converge(("converge",)) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(_MARKER)
    assert len(captured.err.splitlines()) == 1


def test_signal_returncode_is_preserved(capsys: pytest.CaptureFixture[str]) -> None:
    completed = subprocess.CompletedProcess(("converge",), -15, "", "signal-probe\n")
    with patch("automation.deploy_reconcile_cli.subprocess.run", return_value=completed):
        assert run_converge(("converge",)) == -15

    assert capsys.readouterr().err == _MARKER + "signal-probe\n"


@pytest.mark.parametrize("command", [None, ()])
def test_default_command_and_subprocess_options_are_preserved(
    command: tuple[str, ...] | None, capsys: pytest.CaptureFixture[str],
) -> None:
    completed = subprocess.CompletedProcess(converge_command(), 0, "", "")
    with patch("automation.deploy_reconcile_cli.subprocess.run", return_value=completed) as run:
        assert run_converge(command) == 0

    run.assert_called_once_with(
        converge_command(), capture_output=True, text=True, check=False, timeout=900.0,
    )
    assert capsys.readouterr().err == ""
