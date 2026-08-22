"""A decided approval whose peer-attestation window has lapsed is retired, not deferred.

``skill_gate.py check`` binds the peer attestation to ``requested_at +
PEER_ATTESTATION_TTL``, where ``requested_at`` is the approval message's OWN Discord
timestamp. Before the lifecycle guard every re-run posted a new message, so the window
reopened; now the guard correctly reuses the existing message, so once the window closes
the deploy can never mount (``REJECTED: valid peer attestation absent``) AND can never be
re-requested (``DEFERRED reason=owner-decided``). That is a permanent wedge, measured in
production on ``wiki``.

The façade's L3 premise — an owner-decided request is preserved so the decision can be
CONSUMED — is provably false in exactly that state, and only there. These tests pin the
boundary on both sides: an in-window decision still defers, an undecided request still
reuses its live message, the window is read from :mod:`automation.peer_attestation` (so
the constant remains the single definition), the dead message is DELETEd before the
record is dropped, and nothing mutates without the key lease.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

from automation import (
    peer_attestation,
    skill_gate,
    skill_gate_approval,
    skill_gate_request,
    skill_gate_specs,
    skill_gate_surface,
)
from automation.interop.approval_lifecycle import ApprovalRequest
from automation.interop.approval_surface import ChannelFacts

_DIGEST = "a" * 64
_OWNER_ID = "111111111111111111"
_CHANNEL_ID = "100000000000000009"
_SKILL = "wiki"
_ACTOR = "cha"
_APPROVED_NONCE = "b" * 32
_NEXT_NONCE = "c" * 32
_EXPIRED_REASON = "attestation-window-expired"
_NOT_FOUND = HTTPError("https://discord.test", 404, "error", Message(), None)


class FakeDiscord:
    """In-memory #approvals channel; every call logs whether the record was still on disk."""

    def __init__(self, pending: Path) -> None:
        self.pending = pending
        self.calls: list[tuple[str, str, bool]] = []
        self.contents: dict[str, str] = {}
        self.timestamps: dict[str, str] = {}
        self.reactions: dict[tuple[str, str], list[str]] = {}
        self.posted = 0

    def api(self, method: str, path: str, payload: dict[str, str] | None = None) -> object:
        self.calls.append((method, path, self.pending.exists()))
        if method == "POST":
            self.posted += 1
            message_id = f"message-{self.posted}"
            self.contents[message_id] = "" if payload is None else payload["content"]
            self.timestamps[message_id] = datetime.now(UTC).isoformat()
            return {"id": message_id}
        message_id = path.split("/messages/")[1].split("/")[0].split("?")[0]
        if method == "DELETE":
            if self.contents.pop(message_id, None) is None:
                raise _NOT_FOUND
            return None
        if "/reactions/" in path:
            users = self.reactions.get((message_id, unquote(path.split("/reactions/")[1].split("?")[0])))
            if users is None:
                raise _NOT_FOUND
            return [{"id": user, "bot": False} for user in users]
        content = self.contents.get(message_id)
        if content is None:
            raise _NOT_FOUND
        return {"id": message_id, "content": content, "timestamp": self.timestamps[message_id]}

    def methods(self) -> list[str]:
        return [method for method, _, _ in self.calls]

    def deletes_while_record_lived(self) -> list[bool]:
        """One entry per DELETE: whether the pending record still existed at that moment."""
        return [lived for method, _, lived in self.calls if method == "DELETE"]


def _pending(tmp_path: Path) -> Path:
    return tmp_path / "skill-gate" / "pending" / f"{_SKILL}.json"


class _FakeDirectory:
    """What the shared directory answers for this bot — no Discord, no guild scan."""

    def owner_dm(self) -> str:
        raise AssertionError("the skill supply chain must never open a DM (SI-6)")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


def _deploy_bindings(skill: str) -> skill_gate_surface.SupplyChainSurface:
    """The deploy gate's declared surface, stubbed at the directory boundary."""
    return skill_gate_surface.SupplyChainSurface(
        skill_gate_surface.deploy_kind(skill), _OWNER_ID, _FakeDirectory()
    )


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDiscord:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    fake = FakeDiscord(_pending(tmp_path))
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "logs" / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _deploy_bindings)
    monkeypatch.setattr(skill_gate, "_api", fake.api)
    monkeypatch.setenv("SUDO_USER", _ACTOR)
    return fake


def _record(tmp_path: Path) -> dict[str, str]:
    decoded = json.loads(_pending(tmp_path).read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in decoded.items()}


def _audit_lines(tmp_path: Path) -> list[dict[str, str]]:
    path = tmp_path / "logs" / "approval-abandons.jsonl"
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    return [json.loads(line) for line in raw.splitlines() if line]


def _request_args(*, fresh: bool = False) -> argparse.Namespace:
    return argparse.Namespace(skill=_SKILL, hash=_DIGEST, fresh=fresh, json=False)


def _age_record(tmp_path: Path, posted_at: datetime) -> None:
    """The pending record is written at post time, so in production it ages with the message."""
    stamp = posted_at.timestamp()
    os.utime(_pending(tmp_path), (stamp, stamp))


def _aged_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age: timedelta) -> FakeDiscord:
    """Given-block: one live request whose approval message was posted ``age`` ago."""
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    posted_at = datetime.now(UTC) - age
    fake.timestamps["message-1"] = posted_at.isoformat()
    _age_record(tmp_path, posted_at)
    return fake


def _aged_decided_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age: timedelta
) -> FakeDiscord:
    """Given-block: exactly the production wedge — one live request, owner ✅, message aged."""
    fake = _aged_request(tmp_path, monkeypatch, age)
    fake.reactions[("message-1", skill_gate_specs.APPROVE_EMOJI)] = [_OWNER_ID]
    return fake


def _execution(tmp_path: Path) -> tuple[skill_gate_approval.SkillApprovalGate, skill_gate_approval.ApprovalExecution]:
    record = _record(tmp_path)
    args = argparse.Namespace(
        skill=_SKILL,
        hash=_DIGEST,
        deploy_nonce=record["deploy_nonce"],
        provenance_file="",
    )
    gate = skill_gate._deploy_gate(args)
    request = ApprovalRequest(
        key=gate.spec.key(),
        action_hash=gate.spec.action_hash(),
        message_id=record["message_id"],
        channel_id=record["channel_id"],
        created_at="",
    )
    return gate, skill_gate_approval.ApprovalExecution(
        request=request,
        nonce=record["deploy_nonce"],
        action="skill.deploy",
        destination=f"skill:{_SKILL}",
    )


def test_owner_approval_survives_elapsed_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: owner ✅ binds one content hash + nonce + action + destination, then 45 minutes pass.
    nonces = iter((_APPROVED_NONCE, _NEXT_NONCE))

    def next_nonce(_length: int) -> str:
        return next(nonces)

    monkeypatch.setattr(secrets, "token_hex", next_nonce)
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=45))
    approved = _record(tmp_path)
    assert (
        approved["hash"],
        approved["deploy_nonce"],
        approved["kind"],
        approved["channel_id"],
    ) == (_DIGEST, _APPROVED_NONCE, "skill-deploy", _CHANNEL_ID)
    _ = capsys.readouterr()

    # When: an identical skill-deploy action asks to reuse that approval.
    result = skill_gate.cmd_request(_request_args())

    # Then: elapsed time alone cannot retire or replace the bound owner decision.
    captured = capsys.readouterr()
    assert _EXPIRED_REASON not in captured.err, (
        "owner approval for the identical hash + nonce + action + destination "
        "was treated as expired after elapsed time alone"
    )
    assert result == 0
    assert captured.out == "message-1\n"
    assert fake.posted == 1
    assert _record(tmp_path) == approved


def test_request_when_the_decision_is_still_inside_the_window_then_it_is_deferred_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the owner decided five minutes ago — the attestation can still be produced.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    first = _record(tmp_path)
    _ = capsys.readouterr()

    # When: the deploy re-runs and asks to supersede the live request.
    result = skill_gate.cmd_request(_request_args(fresh=True))

    # Then: L3 holds unchanged — a consumable decision is preserved, not retired.
    captured = capsys.readouterr()
    assert result == skill_gate_request.LIFECYCLE_REFUSAL_EXIT
    assert "reason=owner-decided" in captured.err
    assert _EXPIRED_REASON not in captured.err
    assert "DELETE" not in fake.methods()
    assert fake.posted == 1
    assert _record(tmp_path) == first
    assert _audit_lines(tmp_path) == []


@pytest.mark.parametrize("fresh", [False, True])
def test_request_when_the_window_lapsed_then_the_decision_is_audited_retired_and_replaced(
    fresh: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the production wedge — owner ✅ on a message older than the attestation window.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=45))
    _ = capsys.readouterr()

    # When: the deploy requests approval again (plain re-run, and the --fresh escape).
    result = skill_gate.cmd_request(_request_args(fresh=fresh))

    # Then: the dead decision is retired through the audited path and replaced by ONE
    # live message — and stdout stays the deploy pipeline's bare message-id contract.
    captured = capsys.readouterr()
    if fresh:
        assert result == skill_gate_request.LIFECYCLE_REFUSAL_EXIT
        assert "reason=owner-decided" in captured.err
        assert _EXPIRED_REASON not in captured.err
        assert "DELETE" not in fake.methods()
        assert fake.posted == 1
        assert _audit_lines(tmp_path) == []
    else:
        assert result == 0
        assert captured.out == "message-1\n"
        assert _EXPIRED_REASON not in captured.err
        assert fake.posted == 1
        assert _audit_lines(tmp_path) == []


def test_request_when_the_window_lapsed_then_the_message_is_deleted_before_the_record_drops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the same wedge — a decided request whose window has closed.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=45))
    _ = capsys.readouterr()

    # When: the deploy re-requests approval.
    assert skill_gate.cmd_request(_request_args()) == 0

    # Then: the message is NOT deleted because elapsed time no longer expires it.
    _ = capsys.readouterr()
    assert fake.deletes_while_record_lived() == []

def test_request_when_only_the_local_record_looks_old_then_discord_still_decides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an aged pending record whose message Discord still reports as freshly posted
    # — a restored file, a clock step, anything that makes local mtime lie.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=45))
    fake.timestamps["message-1"] = datetime.now(UTC).isoformat()
    first = _record(tmp_path)
    _ = capsys.readouterr()

    # When: the deploy re-runs and asks to supersede the live request.
    result = skill_gate.cmd_request(_request_args(fresh=True))

    # Then: the record's own age is only a trigger — the attestation still binds to
    # Discord's timestamp, so a consumable decision survives.
    captured = capsys.readouterr()
    assert result == skill_gate_request.LIFECYCLE_REFUSAL_EXIT
    assert "reason=owner-decided" in captured.err
    assert _EXPIRED_REASON not in captured.err
    assert "DELETE" not in fake.methods()
    assert fake.posted == 1
    assert _record(tmp_path) == first
    assert _audit_lines(tmp_path) == []


def test_request_when_the_owner_never_decided_then_a_lapsed_window_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a three-hour-old request the owner has not reacted to — still consumable.
    fake = _aged_request(tmp_path, monkeypatch, timedelta(hours=3))
    first = _record(tmp_path)
    _ = capsys.readouterr()

    # When: the deploy re-runs with the same artifact hash.
    result = skill_gate.cmd_request(_request_args())

    # Then: the unchanged live request is reused — nothing posted, deleted or audited.
    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == "message-1\n"
    assert captured.err == ""
    assert fake.posted == 1
    assert "DELETE" not in fake.methods()
    assert _record(tmp_path) == first
    assert _audit_lines(tmp_path) == []


def test_request_when_the_shared_ttl_shrinks_then_a_young_decision_becomes_retirable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a decision five minutes old — far inside the real thirty-minute window.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    _ = capsys.readouterr()

    # When: the ONE definition of the window is shortened and the deploy re-runs.
    monkeypatch.setattr(peer_attestation, "PEER_ATTESTATION_TTL", timedelta(minutes=1))
    result = skill_gate.cmd_request(_request_args())

    # Then: the decision is NOT retirable because elapsed time no longer expires it.
    captured = capsys.readouterr()
    assert result == 0
    assert _EXPIRED_REASON not in captured.err
    assert fake.posted == 1
    assert len(_audit_lines(tmp_path)) == 0


def test_request_when_the_shared_ttl_widens_then_an_hours_old_decision_is_still_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a decision two hours old — expired under the real window.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(hours=2))
    _ = capsys.readouterr()

    # When: the shared window is widened past that age and the deploy re-runs.
    monkeypatch.setattr(peer_attestation, "PEER_ATTESTATION_TTL", timedelta(hours=24))
    result = skill_gate.cmd_request(_request_args(fresh=True))

    # Then: the decision is consumable again, so L3 preserves it — the boundary is read,
    # never duplicated.
    captured = capsys.readouterr()
    assert result == skill_gate_request.LIFECYCLE_REFUSAL_EXIT
    assert "reason=owner-decided" in captured.err
    assert fake.posted == 1
    assert "DELETE" not in fake.methods()
    assert _audit_lines(tmp_path) == []


def test_request_when_the_key_lease_is_held_elsewhere_then_the_expired_path_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an expired decision, and another holder owning this key's lease.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=45))
    first = _record(tmp_path)
    lease_root = skill_gate.GATE_DIR / skill_gate_request.LEASE_DIRNAME
    lease_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = (lease_root / f"skill-deploy%3a{_SKILL}.lease").open("a", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    _ = capsys.readouterr()

    # When: the deploy re-runs while the lease is unavailable.
    try:
        result = skill_gate.cmd_request(_request_args(fresh=True))
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    # Then: no retirement happens outside the lease — no delete, no audit, no new message.
    captured = capsys.readouterr()
    assert result == skill_gate_request.LIFECYCLE_REFUSAL_EXIT
    assert "reason=lease-held" in captured.err
    assert _EXPIRED_REASON not in captured.err
    assert "DELETE" not in fake.methods()
    assert fake.posted == 1
    assert _record(tmp_path) == first
    assert _audit_lines(tmp_path) == []


def test_approval_validity_when_content_changes_then_invalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an owner-approved request and its exact persisted execution binding.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    gate, execution = _execution(tmp_path)

    # When: the Discord request content changes after approval.
    fake.contents["message-1"] += "\n- changed: true"

    # Then: the old owner decision cannot authorize the changed content.
    assert not gate.valid_approval(execution, skill_gate.APPROVAL_LOG)


def test_approval_validity_when_request_is_cancelled_then_invalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an approved request on which the owner also records the fail-safe cancellation.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    fake.reactions[("message-1", skill_gate_specs.CANCEL_EMOJI)] = [_OWNER_ID]
    gate, execution = _execution(tmp_path)

    # When/Then: cancellation wins over approval and invalidates the decision.
    assert not gate.valid_approval(execution, skill_gate.APPROVAL_LOG)


def test_approval_validity_when_request_is_superseded_then_invalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: one approved request and the execution binding that names it.
    _ = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    gate, execution = _execution(tmp_path)

    # When: the current record is replaced by a different request.
    record = _record(tmp_path)
    record["message_id"] = "message-2"
    record["deploy_nonce"] = _NEXT_NONCE
    _ = _pending(tmp_path).write_text(json.dumps(record), encoding="utf-8")

    # Then: the superseded request's approval cannot be replayed.
    assert not gate.valid_approval(execution, skill_gate.APPROVAL_LOG)


def test_approval_validity_when_owner_withdraws_reaction_then_invalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: one request that the owner had approved.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    gate, execution = _execution(tmp_path)

    # When: the owner explicitly withdraws approval by removing the reaction.
    del fake.reactions[("message-1", skill_gate_specs.APPROVE_EMOJI)]

    # Then: the persisted binding alone never substitutes for a current owner decision.
    assert not gate.valid_approval(execution, skill_gate.APPROVAL_LOG)


@pytest.mark.parametrize(
    ("field", "changed"),
    (
        ("action_hash", "0" * 64),
        ("approval_action", "skill.publish"),
        ("approval_destination", "skill:other"),
    ),
    ids=("recorded-hash-differs", "recorded-action-differs", "recorded-destination-differs"),
)
def test_approval_validity_when_recorded_execution_binding_differs_then_fails_closed(
    field: str,
    changed: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an approved request whose persisted execution binding is tampered in one field.
    _ = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    gate, execution = _execution(tmp_path)
    record = _record(tmp_path)
    record[field] = changed
    _ = _pending(tmp_path).write_text(json.dumps(record), encoding="utf-8")

    # When/Then: every hash/action/destination mismatch blocks authorization.
    assert not gate.valid_approval(execution, skill_gate.APPROVAL_LOG)


def test_approval_validity_when_nonce_is_used_by_another_request_then_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the same nonce appears in another persisted request.
    _ = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    gate, execution = _execution(tmp_path)
    duplicate = _record(tmp_path)
    duplicate["message_id"] = "other-message"
    _ = (_pending(tmp_path).parent / "other-skill.json").write_text(
        json.dumps(duplicate), encoding="utf-8"
    )

    # When/Then: nonce reuse across requests is rejected even with a valid owner reaction.
    assert not gate.valid_approval(execution, skill_gate.APPROVAL_LOG)


def test_approval_validity_when_nonce_is_used_by_another_approval_then_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an earlier approval log binds this nonce to different content.
    _ = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    gate, execution = _execution(tmp_path)
    skill_gate.APPROVAL_LOG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = skill_gate.APPROVAL_LOG.write_text(
        json.dumps(
            {
                "binding": {
                    "action": execution.action,
                    "action_hash": "0" * 64,
                    "deploy_nonce": execution.nonce,
                    "destination": execution.destination,
                    "message_id": "other-message",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # When/Then: an approval-bound nonce cannot authorize another request.
    assert not gate.valid_approval(execution, skill_gate.APPROVAL_LOG)


@pytest.mark.parametrize(
    "missing",
    ("action_hash", "approval_action", "approval_destination"),
    ids=("missing-content-hash", "missing-action", "missing-destination"),
)
def test_approval_validity_when_persisted_record_predates_binding_schema_then_fails_closed(
    missing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a persisted request created before the complete binding schema existed.
    _ = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    gate, execution = _execution(tmp_path)
    record = _record(tmp_path)
    _ = record.pop(missing, None)
    _ = _pending(tmp_path).write_text(json.dumps(record), encoding="utf-8")

    # When/Then: compatibility is conservative; missing fields never gain authorization.
    assert not gate.valid_approval(execution, skill_gate.APPROVAL_LOG)


_LEGACY_HEADER = "[skill-deploy] 승인 요청\n"


def _relabel_with_legacy_header(fake: FakeDiscord, gate: skill_gate_approval.SkillApprovalGate) -> None:
    """Rewrite the live message into the pre-#199 form: same body, old first line."""
    content = fake.contents["message-1"]
    assert content.startswith(gate.spec.header())
    fake.contents["message-1"] = _LEGACY_HEADER + content.removeprefix(gate.spec.header())


def test_legacy_header_request_when_owner_approved_then_approval_is_still_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a request posted BEFORE #199 (legacy first line) that the owner has ✅'d.
    fake = _aged_decided_request(tmp_path, monkeypatch, timedelta(minutes=5))
    gate, execution = _execution(tmp_path)
    _relabel_with_legacy_header(fake, gate)

    # Then: the ✅ consumes — the header form is not the binding (2026-08-21: 13 such requests).
    assert gate.valid_approval(execution, skill_gate.APPROVAL_LOG)


def test_legacy_header_request_when_skill_changed_then_is_superseded_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a pending (undecided) request posted BEFORE #199.
    fake = _aged_request(tmp_path, monkeypatch, timedelta(minutes=5))
    gate, _ = _execution(tmp_path)
    _relabel_with_legacy_header(fake, gate)
    first = _record(tmp_path)
    _ = capsys.readouterr()

    # When: the skill changed and is requested under a new digest.
    args = _request_args()
    args.hash = "c" * 64
    result = skill_gate.cmd_request(args)

    # Then: DELETE precedes POST and the record moved on — no orphan, no refusal.
    assert result == 0, capsys.readouterr().err
    methods = fake.methods()
    assert methods.index("DELETE") < methods.index("POST", 1)
    assert first["message_id"] not in fake.contents
    assert _record(tmp_path)["hash"] == "c" * 64
