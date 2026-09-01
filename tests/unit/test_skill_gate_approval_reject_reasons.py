"""Every approval-binding refusal names its OWN cause, and the record outlives it.

2026-08-29, ``skill-deploy:proposal`` resumed 15 times against one journal line —
``REJECTED: owner approval binding invalid`` — while the pending record that would
have explained it was superseded away. ``valid_approval`` collapses a dozen distinct
refusals (record absent, hash tampered, nonce reused, owner reaction withdrawn, …)
into one bare ``False``, so the operator could not tell WHICH check said no, and the
evidence was gone before anyone looked.

These tests pin both halves of the fix: one machine-greppable
``APPROVAL-BINDING-REJECT:<cause>`` token per failing branch (exactly one token per
rejection, never two branches sharing a token), and a 0600 copy of the pending record
written under ``pending-rejected/`` at rejection time — BEFORE any later supersede can
delete it.

Separate file on purpose: ``test_skill_gate_expired_attestation_window.py`` and
``test_skill_gate_legacy_pending_migration.py`` pin the WINDOW and MIGRATION contracts
and only ever assert the outward ``bool``; the diagnosability contract (stderr tokens,
preserved evidence) is a new surface and is asserted here so those replay-pinned
suites keep their exact assertions.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import stat
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

from automation import (
    skill_gate,
    skill_gate_approval,
    skill_gate_refresh,
    skill_gate_specs,
    skill_gate_surface,
)
from automation.interop.approval_surface import ChannelFacts

_DIGEST = "a" * 64
_OWNER_ID = "111111111111111111"
_CHANNEL_ID = "100000000000000009"
_OTHER_CHANNEL_ID = "100000000000000077"
_SKILL = "proposal"
_ACTOR = "cha"
_MESSAGE_ID = "message-1"
_NOT_FOUND = HTTPError("https://discord.test", 404, "error", Message(), None)
_SERVER_ERROR = HTTPError("https://discord.test", 500, "error", Message(), None)


class FakeDiscord:
    """In-memory #approvals channel; ``outage`` turns every read unverifiable."""

    def __init__(self) -> None:
        self.contents: dict[str, str] = {}
        self.reactions: dict[tuple[str, str], list[str]] = {}
        self.posted = 0
        self.outage = False

    def api(self, method: str, path: str, payload: dict[str, str] | None = None) -> object:
        if self.outage:
            raise _SERVER_ERROR
        if method == "POST":
            self.posted += 1
            message_id = f"message-{self.posted}"
            self.contents[message_id] = "" if payload is None else payload["content"]
            return {"id": message_id}
        message_id = path.split("/messages/")[1].split("/")[0].split("?")[0]
        if method == "DELETE":
            if self.contents.pop(message_id, None) is None:
                raise _NOT_FOUND
            return None
        if "/reactions/" in path:
            # Discord answers 200 [] for an emoji nobody used; 404 means the MESSAGE is
            # gone (skill_gate._owner_reacted depends on exactly that distinction).
            if message_id not in self.contents:
                raise _NOT_FOUND
            emoji = unquote(path.split("/reactions/")[1].split("?")[0])
            return [{"id": user, "bot": False} for user in self.reactions.get((message_id, emoji), [])]
        content = self.contents.get(message_id)
        if content is None:
            raise _NOT_FOUND
        return {"id": message_id, "content": content}


class _FakeDirectory:
    def owner_dm(self) -> str:
        raise AssertionError("the skill supply chain must never open a DM (SI-6)")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


def _deploy_bindings(skill: str) -> skill_gate_surface.SupplyChainSurface:
    return skill_gate_surface.SupplyChainSurface(
        skill_gate_surface.deploy_kind(skill), _OWNER_ID, _FakeDirectory()
    )


def _gate_dir(tmp_path: Path) -> Path:
    return tmp_path / "skill-gate"


def _pending(tmp_path: Path) -> Path:
    return _gate_dir(tmp_path) / "pending" / f"{_SKILL}.json"


def _preserved(tmp_path: Path) -> Path:
    return _gate_dir(tmp_path) / skill_gate_approval.PENDING_REJECTED_DIRNAME


def _record(tmp_path: Path) -> dict[str, str]:
    decoded = json.loads(_pending(tmp_path).read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in decoded.items()}


def _rewrite(tmp_path: Path, record: dict[str, str]) -> None:
    _ = _pending(tmp_path).write_text(json.dumps(record), encoding="utf-8")


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDiscord:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    fake = FakeDiscord()
    monkeypatch.setattr(skill_gate, "GATE_DIR", _gate_dir(tmp_path))
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "logs" / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _deploy_bindings)
    monkeypatch.setattr(skill_gate, "_api", fake.api)
    monkeypatch.setenv("SUDO_USER", _ACTOR)
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    return fake


def _approved_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDiscord:
    """Given-block: one live deploy request the owner has ✅'d — the production shape."""
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(argparse.Namespace(skill=_SKILL, hash=_DIGEST, fresh=False, json=False)) == 0
    fake.reactions[(_MESSAGE_ID, skill_gate_specs.APPROVE_EMOJI)] = [_OWNER_ID]
    return fake


def _execution(
    tmp_path: Path,
) -> tuple[skill_gate_approval.SkillApprovalGate, skill_gate_approval.ApprovalExecution]:
    record = _record(tmp_path)
    args = argparse.Namespace(
        skill=_SKILL,
        hash=_DIGEST,
        message_id=record["message_id"],
        deploy_nonce=record["deploy_nonce"],
        provenance_file="",
    )
    gate = skill_gate._deploy_gate(args)
    return gate, skill_gate._approval_execution(gate, args)


def _refresh_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        skill=_SKILL,
        hash=_DIGEST,
        message_id=_MESSAGE_ID,
        deploy_nonce=_record(tmp_path)["deploy_nonce"],
        provenance_file="",
        injection_file="",
        peer_attest_mode="discord",
        peer_attest_public_key="",
        peer_attestation_stdin=False,
    )


def _tokens(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith(f"{skill_gate_approval.REJECT_TOKEN}:")]


_Tamper = Callable[[Path, FakeDiscord], None]


def _drop_record(tmp_path: Path, _fake: FakeDiscord) -> None:
    _pending(tmp_path).unlink()


def _corrupt_record(tmp_path: Path, _fake: FakeDiscord) -> None:
    _ = _pending(tmp_path).write_text("{ not json", encoding="utf-8")


def _strip_nonce(tmp_path: Path, _fake: FakeDiscord) -> None:
    record = _record(tmp_path)
    _ = record.pop("deploy_nonce", None)
    _rewrite(tmp_path, record)


def _set_field(tmp_path: Path, name: str, value: str) -> None:
    record = _record(tmp_path)
    record[name] = value
    _rewrite(tmp_path, record)


def _field(name: str, value: str) -> _Tamper:
    def tamper(tmp_path: Path, _fake: FakeDiscord) -> None:
        _set_field(tmp_path, name, value)

    return tamper


def _sibling_reusing_the_nonce(tmp_path: Path, _fake: FakeDiscord) -> None:
    sibling = dict(_record(tmp_path))
    sibling["message_id"] = "message-9"
    _ = (_pending(tmp_path).parent / "other-skill.json").write_text(
        json.dumps(sibling), encoding="utf-8"
    )


def _sibling_unreadable(tmp_path: Path, _fake: FakeDiscord) -> None:
    _ = (_pending(tmp_path).parent / "other-skill.json").write_text("{ not json", encoding="utf-8")


def _approval_log_unreadable(tmp_path: Path, _fake: FakeDiscord) -> None:
    skill_gate.APPROVAL_LOG.mkdir(mode=0o700, parents=True, exist_ok=True)


def _approval_log_rebinds_the_nonce(tmp_path: Path, _fake: FakeDiscord) -> None:
    record = _record(tmp_path)
    skill_gate.APPROVAL_LOG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = skill_gate.APPROVAL_LOG.write_text(
        json.dumps(
            {
                "binding": {
                    "action": skill_gate_specs.DEPLOY_ACTION,
                    "action_hash": "0" * 64,
                    "deploy_nonce": record["deploy_nonce"],
                    "destination": f"skill:{_SKILL}",
                    "message_id": "message-9",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _message_deleted(_tmp_path: Path, fake: FakeDiscord) -> None:
    del fake.contents[_MESSAGE_ID]


def _message_content_changed(_tmp_path: Path, fake: FakeDiscord) -> None:
    fake.contents[_MESSAGE_ID] += "\n- changed: true"


def _owner_cancelled(_tmp_path: Path, fake: FakeDiscord) -> None:
    fake.reactions[(_MESSAGE_ID, skill_gate_specs.CANCEL_EMOJI)] = [_OWNER_ID]


def _owner_withdrew(_tmp_path: Path, fake: FakeDiscord) -> None:
    del fake.reactions[(_MESSAGE_ID, skill_gate_specs.APPROVE_EMOJI)]


def _surface_outage(_tmp_path: Path, fake: FakeDiscord) -> None:
    fake.outage = True


#: One row per False-returning branch reachable through the record/surface state.
_STATE_CASES: tuple[tuple[str, _Tamper], ...] = (
    ("record-absent", _drop_record),
    ("record-unreadable", _corrupt_record),
    ("legacy-binding-incomplete", _strip_nonce),
    ("sha-mismatch", _field("action_hash", "0" * 64)),
    ("message-id-mismatch", _field("message_id", "message-9")),
    ("nonce-mismatch", _field("deploy_nonce", "d" * 32)),
    ("action-mismatch", _field("approval_action", "skill.publish")),
    ("destination-mismatch", _field("approval_destination", "skill:other")),
    ("pending-nonce-reused", _sibling_reusing_the_nonce),
    ("pending-record-unreadable", _sibling_unreadable),
    ("approval-log-unreadable", _approval_log_unreadable),
    ("approval-log-nonce-rebound", _approval_log_rebinds_the_nonce),
    ("message-missing", _message_deleted),
    ("probe-binding-mismatch", _message_content_changed),
    ("owner-cancelled", _owner_cancelled),
    ("owner-reaction-absent", _owner_withdrew),
    ("surface-unverifiable", _surface_outage),
)


@pytest.mark.parametrize(("cause", "tamper"), _STATE_CASES, ids=[case for case, _ in _STATE_CASES])
def test_valid_approval_when_a_branch_refuses_then_it_emits_its_own_token(
    cause: str,
    tamper: _Tamper,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an owner-approved request whose binding is broken in exactly one way.
    fake = _approved_request(tmp_path, monkeypatch)
    gate, execution = _execution(tmp_path)
    tamper(tmp_path, fake)
    _ = capsys.readouterr()

    # When: the deploy asks whether that owner decision authorizes this execution.
    approved = gate.valid_approval(execution, skill_gate.APPROVAL_LOG)

    # Then: the refusal stands AND names the single branch that produced it.
    assert not approved
    assert _tokens(capsys.readouterr().err) == [
        f"{skill_gate_approval.REJECT_TOKEN}:{cause}"
    ]


def test_valid_approval_when_the_request_key_differs_then_names_the_key_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an execution carrying another gate's key over this gate's record.
    _ = _approved_request(tmp_path, monkeypatch)
    gate, execution = _execution(tmp_path)
    foreign = dataclasses.replace(execution.request, key="skill-deploy:other")
    _ = capsys.readouterr()

    # When/Then: the key check refuses under its own token.
    assert not gate.valid_approval(
        dataclasses.replace(execution, request=foreign), skill_gate.APPROVAL_LOG
    )
    assert _tokens(capsys.readouterr().err) == [
        f"{skill_gate_approval.REJECT_TOKEN}:key-mismatch"
    ]


def test_valid_approval_when_the_channel_differs_then_names_the_channel_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an execution bound to a channel the record never posted to (SI-1).
    _ = _approved_request(tmp_path, monkeypatch)
    gate, execution = _execution(tmp_path)
    elsewhere = dataclasses.replace(execution.request, channel_id=_OTHER_CHANNEL_ID)
    _ = capsys.readouterr()

    # When/Then: the channel check refuses under its own token.
    assert not gate.valid_approval(
        dataclasses.replace(execution, request=elsewhere), skill_gate.APPROVAL_LOG
    )
    assert _tokens(capsys.readouterr().err) == [
        f"{skill_gate_approval.REJECT_TOKEN}:channel-mismatch"
    ]


def test_reject_causes_are_distinct_greppable_tokens() -> None:
    # Given/When/Then: no two branches can ever be confused for one another in a journal.
    values = [cause.value for cause in skill_gate_approval.RejectCause]
    assert len(values) == len(set(values))
    assert all(value == value.lower() and " " not in value and value for value in values)
    covered = {cause for cause, _ in _STATE_CASES} | {"key-mismatch", "channel-mismatch"}
    assert covered <= set(values)


def test_valid_approval_when_the_binding_holds_then_no_token_is_emitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the untouched approved request.
    _ = _approved_request(tmp_path, monkeypatch)
    gate, execution = _execution(tmp_path)
    _ = capsys.readouterr()

    # When/Then: authorization succeeds and the diagnostic channel stays silent.
    assert gate.valid_approval(execution, skill_gate.APPROVAL_LOG)
    assert _tokens(capsys.readouterr().err) == []


def test_refresh_when_the_owner_binding_is_invalid_then_the_pending_record_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the production wedge — owner ✅ present, execution binding no longer matching.
    _ = _approved_request(tmp_path, monkeypatch)
    args = _refresh_args(tmp_path)
    _set_field(tmp_path, "approval_destination", "skill:other")
    doomed = _pending(tmp_path).read_text(encoding="utf-8")
    _ = capsys.readouterr()

    # When: the peer-attestation refresh path re-judges the owner approval.
    result = skill_gate_refresh.refresh_required(args)

    # Then: it refuses, names the branch, and the record is copied aside 0600 first.
    captured = capsys.readouterr()
    assert result == 1
    assert "REJECTED: owner approval binding invalid" in captured.err
    assert _tokens(captured.err) == [
        f"{skill_gate_approval.REJECT_TOKEN}:destination-mismatch"
    ]
    copies = sorted(_preserved(tmp_path).glob("*.json"))
    assert len(copies) == 1
    assert copies[0].read_text(encoding="utf-8") == doomed
    assert stat.S_IMODE(copies[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(_preserved(tmp_path).stat().st_mode) == 0o700
    assert copies[0].name.startswith(f"{_SKILL}-")
    assert copies[0].name.endswith("-destination-mismatch.json")


def test_preserved_copy_when_the_pending_record_is_dropped_then_the_evidence_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a rejection that already preserved the record.
    _ = _approved_request(tmp_path, monkeypatch)
    args = _refresh_args(tmp_path)
    _set_field(tmp_path, "approval_destination", "skill:other")
    gate, execution = _execution(tmp_path)
    assert skill_gate_refresh.refresh_required(args) == 1
    _ = capsys.readouterr()

    # When: the next run supersedes that request and deletes the pending record.
    gate.drop(execution.request)

    # Then: the cause is still reproducible from the preserved copy.
    assert not _pending(tmp_path).exists()
    assert len(sorted(_preserved(tmp_path).glob("*.json"))) == 1
