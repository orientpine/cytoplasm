"""mail-daily-digest watcher: structured single-line failure marker + bounded retry.

The 2026-07-31 incident: the 08:00 KST digest failed when the 7th item's
``glm-main`` classification timed out during item build (before any Discord
send). The owner was never told, because the cron was registered
``--deliver local`` (0 delivery targets) so the wrapper's alert line went
nowhere. The watcher's retry, meanwhile, keyed on a free-form Korean substring
(``digest DM 발송 실패``) that the producer no longer emits.

The watcher now speaks one contract with the CLI: every failure surfaces as
exactly one structured ``DIGEST-FAIL stage=... retry_safe=... code=...`` line on
stderr. The watcher passes a child marker through verbatim, or synthesizes a
``stage=runner`` marker when the child produced none. In-tick retry fires only
for ``retry_safe=true`` markers; a ``retry_safe=false`` failure (a delivery that
may already have sent some Discord chunks, or a build item that may already have
delegated a calendar draft) is never auto-replayed, so the owner never receives
a duplicate.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "mail" / "scripts"

_RETRY_SAFE = "DIGEST-FAIL stage=deliver retry_safe=true code=dns detail=name resolution failed"
_RETRY_UNSAFE = (
    "DIGEST-FAIL stage=deliver retry_safe=false code=discord_delivery_failed "
    "detail=HTTP Error 500: Internal Server Error"
)
_BUILD_UNSAFE = "DIGEST-FAIL stage=build retry_safe=false code=llm_call_failed detail=glm-main timed out"


def _load_watch_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mail_digest_watch_retry", _SCRIPTS / "mail_digest_watch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watch = _load_watch_module()


def _stub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcomes: list[tuple[int, str]]
) -> list[list[str]]:
    """Run the watcher against a fake mounted CLI returning scripted (rc, stderr)."""
    cli = tmp_path / "triage_cli.py"
    _ = cli.write_text("", encoding="utf-8")
    monkeypatch.setattr(watch, "CLI", cli)
    monkeypatch.setattr(watch, "_RETRY_DELAYS_S", (0, 0, 0))
    calls: list[list[str]] = []
    queue = list(outcomes)

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        returncode, stderr = queue.pop(0) if queue else (4, _RETRY_UNSAFE)
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    monkeypatch.setattr(watch.subprocess, "run", fake_run)
    return calls


def test_success_is_a_silent_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _stub(monkeypatch, tmp_path, [(0, "")])
    assert watch.main() == 0
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""  # no cron alert on success


def test_retry_safe_marker_retries_until_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _stub(monkeypatch, tmp_path, [(4, _RETRY_SAFE), (0, "")])
    assert watch.main() == 0
    assert len(calls) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""  # succeeded on retry — silent


def test_retry_unsafe_deliver_marker_is_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _stub(monkeypatch, tmp_path, [(4, _RETRY_UNSAFE)])
    assert watch.main() == 1
    assert len(calls) == 1  # the owner DM must never be sent twice
    captured = capsys.readouterr()
    # The child marker is passed through verbatim, on stdout (cron-delivered), one line.
    assert captured.err == ""
    assert captured.out.strip().splitlines() == [_RETRY_UNSAFE]


def test_build_marker_is_passed_through_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _stub(monkeypatch, tmp_path, [(4, _BUILD_UNSAFE)])
    assert watch.main() == 1
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.strip().splitlines() == [_BUILD_UNSAFE]


def test_unmarked_child_failure_is_synthesized_as_stage_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _stub(monkeypatch, tmp_path, [(3, "Traceback: KeyError 'x'")])
    assert watch.main() == 1
    assert len(calls) == 1  # a non-marker failure is not a retry_safe signal
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("DIGEST-FAIL stage=runner retry_safe=false code=child_exit")
    assert "child_rc=3" in lines[0]
    assert captured.err == ""


def test_missing_cli_is_a_structured_runner_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(watch, "CLI", tmp_path / "absent.py")
    assert watch.main() == 1
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("DIGEST-FAIL stage=runner retry_safe=false code=not_mounted")
    assert captured.err == ""


def test_marker_detail_masks_addresses_and_long_digits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    leaky = "DIGEST-FAIL stage=build retry_safe=false code=llm_call_failed detail=someone@example.com uid=1234567"
    _stub(monkeypatch, tmp_path, [(4, leaky)])
    assert watch.main() == 1
    printed = capsys.readouterr().out
    assert "someone@example.com" not in printed and "1234567" not in printed
    assert "[MASKED-EMAIL]" in printed and "[MASKED-NUM]" in printed


def test_retries_are_bounded_and_alert_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Every attempt returns a retry_safe marker; the watcher must stop and alert once.
    calls = _stub(monkeypatch, tmp_path, [])  # default queue outcome is retry_unsafe → but override:
    monkeypatch.setattr(watch, "_RETRY_DELAYS_S", (0, 0, 0))

    def always_retry_safe(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 4, "", _RETRY_SAFE)

    monkeypatch.setattr(watch.subprocess, "run", always_retry_safe)
    assert watch.main() == 1
    assert len(calls) == 1 + len(watch._RETRY_DELAYS_S)
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_configured_backoff_stays_within_the_cron_window() -> None:
    delays = _load_watch_module()._RETRY_DELAYS_S
    assert delays and all(delay > 0 for delay in delays)
    assert sum(delays) < 600  # a daily job must not camp on the scheduler
