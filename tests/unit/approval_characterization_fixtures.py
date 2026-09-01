"""Production-shaped fixtures for the approval-surface characterization locks
(AS-0.2, split out under AS-1.11).

Helper module, not a test module: the name carries no ``test_`` prefix so pytest
does not collect it.

Importing this module puts the repo root and ``skills/mail/scripts`` on
``sys.path``. Every characterization test module therefore imports it BEFORE it
imports ``triage_*`` — that import order is load-bearing, not cosmetic.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

import pytest

from approval_conformance_inventory import _REPO

sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_binding  # noqa: E402
import triage_confirm  # noqa: E402
import triage_gate  # noqa: E402
from automation.interop.approval_surface import ChannelFacts  # noqa: E402

# Production surfaces, used as fixtures: the owner, this bot's DM with them, the
# guild #approvals channel, and a SECOND approvals channel an older message may
# still live in — the precedence case the migration exists to serve.
OWNER_ID: Final = "280680578314010625"
OWNER_DM_CHANNEL_ID: Final = "1526487935975952385"
APPROVALS_CHANNEL_ID: Final = "1528936606856122421"
BOUND_APPROVALS_CHANNEL_ID: Final = "1528936606856122422"
AGENT_CHAT_CHANNEL_ID: Final = "1526487935975952390"
AGENT_CHAT_THREAD_ID: Final = "1526487935975952391"
_BINDING_FIELDS: Final = ("kind", "surface", "channel_id", "policy_version")


class _FakeDirectory:
    """Hand-written ``ChannelDirectory`` that counts every question a gate asks."""

    def __init__(self) -> None:
        self.approvals_calls = 0
        self.dm_calls = 0
        self.thread_calls = 0
        self.described: list[str] = []

    def owner_dm(self) -> str:
        self.dm_calls += 1
        return OWNER_DM_CHANNEL_ID

    def skill_approvals(self) -> str:
        self.approvals_calls += 1
        return APPROVALS_CHANNEL_ID

    def agent_chat(self) -> str:
        return AGENT_CHAT_CHANNEL_ID

    def agent_chat_thread(self, kind: object) -> str:
        self.thread_calls += 1
        return AGENT_CHAT_THREAD_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        self.described.append(channel_id)
        if channel_id == OWNER_DM_CHANNEL_ID:
            return ChannelFacts(1, "", (OWNER_ID,))
        if channel_id == AGENT_CHAT_THREAD_ID:
            return ChannelFacts(11, "승인-mail-reply", (), AGENT_CHAT_CHANNEL_ID)
        return ChannelFacts(0, "approvals", ())


def _bind_mail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _FakeDirectory:
    """Confine the mail gate to tmp_path and to a fake directory — never Discord."""
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "mail-gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail-home"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    directory = _FakeDirectory()
    monkeypatch.setattr(triage_binding, "approval_directory", lambda: directory)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    return directory


def _mail_draft(kind: str) -> dict:
    return triage_gate.create_draft(
        uid="uid-1", sender="발신자 <s@example.invalid>", mail_subject="문의",
        to="owner@example.invalid", subject="Re: 문의", body="본문", sensitive=False,
        tags=(), category="important", flags=("reply_needed",), kind=kind,
    )
