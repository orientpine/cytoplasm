"""Nightly re-ingest replays kanban create; the block step must survive it.

``kanban create --idempotency-key`` intentionally returns the existing card on
replay, but ``hermes kanban block`` refuses an already-blocked card with rc=1
(``cannot block t_…`` — measured 2026-08-31 on the primary node: card
t_280e8ca9, created 08-28 by meeting-skill, killed the 08-29 and 08-31
meeting-pending-transcript-watch runs through ``check=True``). The old
``capture_output`` + ``check=True`` pair also swallowed hermes stderr and put
the full create argv — card body included — into the CalledProcessError that
lands in the owner-facing cron banner. New file per tests/AGENTS.md: FS3
completion records pin the output of existing test files.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "meeting" / "scripts"))

import meeting_actions  # noqa: E402
import meeting_cli  # noqa: E402

_BODY_MARKER = "MEETING-BODY-MUST-NOT-LEAK"
_CARD = meeting_actions.PlannedCard(
    title="공정표 취합", body=_BODY_MARKER, idempotency_key="k1"
)


def _install(monkeypatch, dispatch):
    def fake_run(argv, capture_output=True, timeout=None, cwd=None, check=False):
        rc, out, err = dispatch(argv)
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, argv, out, err)
        return subprocess.CompletedProcess(argv, rc, out, err)

    monkeypatch.setattr(meeting_cli.subprocess, "run", fake_run)


def _dispatch(*, create=(0, json.dumps({"id": "t_9"}).encode(), b""),
              block=(0, b"", b""), show=(0, b"", b"")):
    table = {"create": create, "block": block, "show": show}

    def resolve(argv):
        assert argv[0] == "hermes" and argv[1] == "kanban"
        return table[argv[2]]

    return resolve


def test_create_and_block_success_returns_card_id(monkeypatch):
    _install(monkeypatch, _dispatch())
    assert meeting_cli._run_kanban(_CARD) == "t_9"


def test_block_replay_on_already_blocked_card_returns_id(monkeypatch, capsys):
    _install(monkeypatch, _dispatch(
        block=(1, b"", b"cannot block t_9"),
        show=(0, b"Task t_9: x\n  status:    blocked\n  assignee:  -\n", b""),
    ))
    assert meeting_cli._run_kanban(_CARD) == "t_9"
    assert "KANBAN-BLOCK-REDUNDANT card=t_9" in capsys.readouterr().out


def test_block_failure_on_unblocked_card_surfaces_hermes_stderr(monkeypatch):
    _install(monkeypatch, _dispatch(
        block=(1, b"", b"cannot block t_9"),
        show=(0, b"Task t_9: x\n  status:    todo\n", b""),
    ))
    with pytest.raises(RuntimeError, match="cannot block t_9"):
        meeting_cli._run_kanban(_CARD)


def test_create_failure_surfaces_stderr_without_card_body(monkeypatch):
    _install(monkeypatch, _dispatch(create=(1, b"", b"boom-hermes")))
    with pytest.raises(RuntimeError, match="boom-hermes") as caught:
        meeting_cli._run_kanban(_CARD)
    assert _BODY_MARKER not in str(caught.value)
