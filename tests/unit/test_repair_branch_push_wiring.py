"""Rollout ③ wiring: a finished repair reaches origin as a branch, or says it did not.

`push_branch` is only half the rule. Until the pipeline calls it, every repair
commit lives in the work clone that the next run resets to origin/main — the
repair is silently lost. These tests fix the call site and, just as importantly,
the failure reporting: a push that did not happen must never look like success.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.repair import repair_ops_cli as cli
from automation.repair.repair_ops_core import RepairOutcome, RepairPhase
from automation.repair.repair_ops_git import RepairOpsError


class _StubAgent:
    def __init__(self, outcome: RepairOutcome) -> None:
        self._outcome = outcome

    def repair(self, ticket_id: str, log: object) -> RepairOutcome:  # noqa: ARG002
        return self._outcome


def _config(tmp_path: Path) -> cli.RepairOpsConfig:
    return cli.RepairOpsConfig(
        "t_abc123", tmp_path / "deploy", tmp_path / "logs", tmp_path / "plans",
        tmp_path / "approvals.jsonl", None, None, tmp_path / "work",
    )


@pytest.fixture
def _wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[str, Path | None]]:
    """Record push attempts instead of touching origin, and supply a write key."""
    pushed: list[tuple[str, Path | None]] = []
    key = tmp_path / "repair_push_key"
    _ = key.write_text("KEY", encoding="utf-8")
    known_hosts = tmp_path / "repair_known_hosts"
    _ = known_hosts.write_text("github.com ssh-ed25519 AAAA", encoding="utf-8")
    monkeypatch.setenv("REPAIR_PUSH_KEY", str(key))
    monkeypatch.setenv("REPAIR_KNOWN_HOSTS", str(known_hosts))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(cli, "private_log", lambda *_a, **_k: None)

    def _fake_push(
        self: object, ticket_id: str, ssh_key: Path | None = None,
        known_hosts: Path | None = None,  # noqa: ARG001
    ) -> str:
        pushed.append((ticket_id, ssh_key))
        return f"repair/{ticket_id}"

    monkeypatch.setattr(cli.RepairWorkClone, "push_branch", _fake_push)
    return pushed


def _run_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: RepairOutcome) -> int:
    monkeypatch.setattr(cli, "_agent", lambda *_a, **_k: _StubAgent(outcome))
    return cli._run(_config(tmp_path), object())  # pyright: ignore[reportArgumentType]


def test_run_when_repair_commits_then_pushes_the_branch_with_the_write_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    _wired: list[tuple[str, Path | None]],
) -> None:
    outcome = RepairOutcome(RepairPhase.COMPLETED, "t_abc123", "deadbeef", None)
    code = _run_with(monkeypatch, tmp_path, outcome)

    assert code == 0
    assert len(_wired) == 1, "a committed repair must be published exactly once"
    ticket, key = _wired[0]
    assert ticket == "t_abc123"
    assert key is not None and key.name == "repair_push_key", "must not fall back to the ops key"
    assert json.loads(capsys.readouterr().out)["branch"] == "repair/t_abc123"


def test_run_when_bank_blocked_then_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    _wired: list[tuple[str, Path | None]],
) -> None:
    outcome = RepairOutcome(RepairPhase.BANK_BLOCKED, "t_abc123", None, None)
    code = _run_with(monkeypatch, tmp_path, outcome)

    assert code == 3, "bank-blocked must keep its existing exit contract"
    assert _wired == [], "a repair that never applied must not reach origin"
    assert json.loads(capsys.readouterr().out)["branch"] is None


def test_run_when_write_key_is_absent_then_fails_loudly_without_using_the_ops_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    _wired: list[tuple[str, Path | None]],
) -> None:
    monkeypatch.setenv("REPAIR_PUSH_KEY", str(tmp_path / "missing_key"))
    outcome = RepairOutcome(RepairPhase.COMPLETED, "t_abc123", "deadbeef", None)
    code = _run_with(monkeypatch, tmp_path, outcome)

    assert code == 4, "an unpublished repair must not report success"
    assert _wired == [], "no key means no push — never a silent read-only fallback"
    reported = json.loads(capsys.readouterr().out)
    assert reported["branch"] is None
    assert reported["push_error"]


def test_run_when_push_fails_then_reports_it_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    _wired: list[tuple[str, Path | None]],
) -> None:
    def _boom(
        self: object, ticket_id: str, ssh_key: Path | None = None,
        known_hosts: Path | None = None,
    ) -> str:
        raise RepairOpsError("git operation failed: remote rejected")

    monkeypatch.setattr(cli.RepairWorkClone, "push_branch", _boom)
    outcome = RepairOutcome(RepairPhase.COMPLETED, "t_abc123", "deadbeef", None)
    code = _run_with(monkeypatch, tmp_path, outcome)

    assert code == 4
    assert json.loads(capsys.readouterr().out)["push_error"]


def test_run_when_e2e_mode_then_never_touches_real_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    _wired: list[tuple[str, Path | None]],
) -> None:
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    outcome = RepairOutcome(RepairPhase.COMPLETED, "t_abc123", "deadbeef", None)
    code = _run_with(monkeypatch, tmp_path, outcome)

    assert code == 0
    assert _wired == [], "E2E runs must not push to the real repository"
    _ = capsys.readouterr()


def test_run_when_known_hosts_is_absent_then_refuses_rather_than_trusting_any_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    _wired: list[tuple[str, Path | None]],
) -> None:
    # Without a pinned host-key database the only way to push is to accept
    # whatever key answers — on the one path that carries a write credential.
    monkeypatch.setenv("REPAIR_KNOWN_HOSTS", str(tmp_path / "missing_known_hosts"))
    outcome = RepairOutcome(RepairPhase.COMPLETED, "t_abc123", "deadbeef", None)
    code = _run_with(monkeypatch, tmp_path, outcome)

    assert code == 4
    assert _wired == []
    assert json.loads(capsys.readouterr().out)["push_error"]
