"""Candidate discovery is not authorization of the timer's tip."""
from __future__ import annotations

from pathlib import Path

import pytest

from automation import owner_notice, release_approval, skill_gate
from automation.interop.approval_types import Probe
from tests.unit.test_release_approval import _pending, _StubGate


@pytest.mark.parametrize("probe", list(Probe))
def test_returns_only_verified_bound_candidate_when_tip_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    probe: Probe,
) -> None:
    # Given: a bound request at another SHA, with a definite or uncertain probe.
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(probe))
    monkeypatch.setattr(owner_notice, "notify_owner", lambda *args, **kwargs: pytest.fail("candidate discovery cannot notify"))
    # When: the timer asks for a candidate, not approval for its current tip.
    code = release_approval.main([
        "decision", "--head", "e" * 40, "--notify-stale", "--completion-candidate",
    ])
    # Then: only APPROVED yields the bound identity, with a distinct nonzero status.
    captured = capsys.readouterr()
    assert code == (3 if probe is Probe.APPROVED else 2)
    assert captured.out == (
        f"{record['head_sha']} {record['version']}\n" if probe is Probe.APPROVED else ""
    )


def test_keeps_decision_bytes_when_candidate_request_matches_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an approved request exactly at the timer's tip.
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.APPROVED))
    # When: the timer uses candidate discovery.
    code = release_approval.main([
        "decision", "--head", record["head_sha"], "--notify-stale", "--completion-candidate",
    ])
    # Then: the original machine-readable output is byte-identical.
    captured = capsys.readouterr()
    assert (code, captured.out, captured.err) == (
        0, "", f"RELEASE-DECISION: approved version={record['version']}\n",
    )


def test_keeps_executed_release_unavailable_when_candidate_matches_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the old bound SHA has already been tagged.
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: pytest.fail("executed approval needs no probe"))
    # When: the timer asks for a candidate.
    code = release_approval.main([
        "decision", "--head", "e" * 40, "--notify-stale", "--completion-candidate",
        "--tagged", record["head_sha"],
    ])
    # Then: no new tag candidate is emitted.
    assert code == 2
    assert capsys.readouterr().out == ""
