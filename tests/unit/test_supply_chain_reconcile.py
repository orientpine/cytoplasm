from __future__ import annotations

import argparse
import fcntl
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

from automation import (
    skill_gate,
    skill_gate_approval,
    skill_gate_request,
    skill_gate_retire,
    skill_gate_specs,
    skill_gate_surface,
)
from automation.interop.approval_lifecycle import ApprovalSurfaceError
from automation.interop.approval_lifecycle import ApprovalRequest, Probe
from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ChannelFacts
from automation.skill_gate_review import review_status_line
from automation.supply_chain_plan import SETTLED, PendingRequest
from automation.supply_chain_reconcile import HOLD, RETIRE_DONE, RUN, Reconciled, reconcile

_DIGEST = "a" * 64
_OTHER_DIGEST = "c" * 64
_OWNER_ID = "111111111111111111"
_CHANNEL_ID = "100000000000000009"
_SKILL = "wiki"
_NOT_FOUND = HTTPError("https://discord.test", 404, "error", Message(), None)
_DEPLOY_BINDING = re.compile(
    "".join(
        (
            r"\A\[skill-deploy\] 승인 요청\n- skill: `(?P<skill>[a-z0-9][a-z0-9-]{1,40})`\n",
            r"- sha256: `(?P<digest>[0-9a-f]{64})`\n- deploy_nonce: `(?P<nonce>[0-9a-f]{32})`\n",
        )
    )
)
DiscordResult = dict[str, str] | list[dict[str, str | bool]] | None


class FakeDiscord:
    """실제 저장·리액션 순서를 보존해야 승인 판정과 CAS 경계를 함께 검증할 수 있다."""

    def __init__(self) -> None:
        self.contents: dict[str, str] = {}
        self.reactions: dict[tuple[str, str], list[str]] = {}
        self.posted: int = 0

    def api(self, method: str, path: str, payload: dict[str, str] | None = None) -> DiscordResult:
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
            emoji = unquote(path.split("/reactions/")[1].split("?")[0])
            users = self.reactions.get((message_id, emoji))
            if users is None:
                raise _NOT_FOUND
            return [{"id": user, "bot": False} for user in users]
        content = self.contents.get(message_id)
        if content is None:
            raise _NOT_FOUND
        return {"id": message_id, "content": content}


class _FakeDirectory:
    def owner_dm(self) -> str:
        raise AssertionError("supply-chain approval must not open a DM")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


class _TestBindings:
    """테스트 게이트도 production과 같은 저장 바인딩 재생 경계를 지나야 한다."""

    kind: ApprovalKind

    def __init__(self, surface: skill_gate_surface.SupplyChainSurface) -> None:
        self.kind = surface.kind
        self._surface: skill_gate_surface.SupplyChainSurface = surface

    def new(self) -> ApprovalBinding:
        return self._surface.new()

    def stored(self, record: Mapping[str, str]) -> ApprovalBinding:
        return self._surface.stored(record)


@dataclass(frozen=True, slots=True)
class GateFactory:
    fake: FakeDiscord
    gate_dir: Path

    def __call__(self, skill: str, digest: str) -> skill_gate_approval.SkillApprovalGate:
        spec = skill_gate_specs.DeploySpec(
            skill=skill,
            digest=digest,
            deploy_nonce="0" * 32,
            review_status=review_status_line(
                self.gate_dir / "review-verdicts.jsonl", skill, digest
            ),
            provenance=skill_gate_specs.Provenance("", "", ""),
            binding=_DEPLOY_BINDING,
        )
        surface = skill_gate_approval.GateSurface(
            self.fake.api,
            self.gate_dir,
            lambda: _OWNER_ID,
            lambda: _deploy_bindings(skill),
        )
        return skill_gate_approval.SkillApprovalGate(surface, spec)


@dataclass(frozen=True, slots=True)
class _TestEnv:
    fake: FakeDiscord
    gates: GateFactory


def _deploy_bindings(skill: str) -> _TestBindings:
    return _TestBindings(
        skill_gate_surface.SupplyChainSurface(
            skill_gate_surface.deploy_kind(skill), _OWNER_ID, _FakeDirectory()
        )
    )


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _TestEnv:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    fake = FakeDiscord()
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "logs" / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _deploy_bindings)
    monkeypatch.setattr(skill_gate, "_api", fake.api)
    return _TestEnv(fake, GateFactory(fake, skill_gate.GATE_DIR))


def _args(digest: str = _DIGEST) -> argparse.Namespace:
    return argparse.Namespace(skill=_SKILL, hash=digest, fresh=False, json=False)


def _pending(tmp_path: Path) -> Path:
    return tmp_path / "skill-gate" / "pending" / f"{_SKILL}.json"


def _request() -> PendingRequest:
    return PendingRequest(f"skill-deploy:{_SKILL}", "skill-deploy", _SKILL, _SKILL)


def _store(tmp_path: Path, digest: str = _DIGEST, *, live: bool = True) -> Path:
    root = tmp_path / "store"
    release = root / "releases" / _SKILL / digest
    release.mkdir(parents=True)
    (root / "live").mkdir()
    if live:
        (root / "live" / _SKILL).symlink_to(release, target_is_directory=True)
    return root


def _approved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _TestEnv:
    env = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_args()) == 0
    env.fake.reactions[("message-1", skill_gate_specs.APPROVE_EMOJI)] = [_OWNER_ID]
    return env


def test_approved_live_digest_is_retired_with_the_stored_triple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    env = _approved(tmp_path, monkeypatch)
    store = _store(tmp_path)
    calls: list[tuple[str, str, str]] = []

    def consume(gate: skill_gate_approval.SkillApprovalGate, message_id: str) -> skill_gate_retire.Retired:
        calls.append((gate.spec.skill, gate.spec.digest, message_id))
        return skill_gate_retire.consume(gate, message_id)

    # When
    result = reconcile(_request(), gate_dir=skill_gate.GATE_DIR, store_root=store, gate_for=env.gates, consume=consume)

    # Then
    assert result.verdict == RETIRE_DONE
    assert not _pending(tmp_path).exists()
    assert calls == [(_SKILL, _DIGEST, "message-1")]


def test_replaced_record_during_consume_is_held_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    env = _approved(tmp_path, monkeypatch)
    store = _store(tmp_path)
    newer = b""

    def replace_then_consume(
        gate: skill_gate_approval.SkillApprovalGate, message_id: str
    ) -> skill_gate_retire.Retired:
        nonlocal newer
        env.fake.reactions.clear()
        assert skill_gate.cmd_request(_args(_OTHER_DIGEST)) == 0
        newer = _pending(tmp_path).read_bytes()
        return skill_gate_retire.consume(gate, message_id)

    # When
    result = reconcile(
        _request(), gate_dir=skill_gate.GATE_DIR, store_root=store,
        gate_for=env.gates, consume=replace_then_consume,
    )

    # Then
    assert result.verdict == HOLD
    assert _pending(tmp_path).read_bytes() == newer


@pytest.mark.parametrize("live", [True, False])
def test_unrealized_live_state_runs_without_consuming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: bool
) -> None:
    # Given
    env = _approved(tmp_path, monkeypatch)
    before = _pending(tmp_path).read_bytes()
    store = _store(tmp_path, _OTHER_DIGEST, live=live)

    def forbidden(
        _gate: skill_gate_approval.SkillApprovalGate, _message_id: str
    ) -> skill_gate_retire.Retired:
        raise AssertionError("consume must not run")

    # When
    result = reconcile(_request(), gate_dir=skill_gate.GATE_DIR, store_root=store, gate_for=env.gates, consume=forbidden)

    # Then
    assert result.verdict == RUN
    assert _pending(tmp_path).read_bytes() == before


def test_non_symlink_live_entry_is_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    env = _approved(tmp_path, monkeypatch)
    before = _pending(tmp_path).read_bytes()
    store = _store(tmp_path, live=False)
    (store / "live" / _SKILL).mkdir()

    # When
    result = reconcile(_request(), gate_dir=skill_gate.GATE_DIR, store_root=store, gate_for=env.gates)

    # Then
    assert result.verdict == HOLD
    assert _pending(tmp_path).read_bytes() == before


def test_cancelled_probe_is_held_without_consuming(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    env = _approved(tmp_path, monkeypatch)
    env.fake.reactions[("message-1", skill_gate_specs.CANCEL_EMOJI)] = [_OWNER_ID]
    before = _pending(tmp_path).read_bytes()
    store = _store(tmp_path)

    def forbidden(
        _gate: skill_gate_approval.SkillApprovalGate, _message_id: str
    ) -> skill_gate_retire.Retired:
        raise AssertionError("consume must not run")

    # When
    result = reconcile(_request(), gate_dir=skill_gate.GATE_DIR, store_root=store, gate_for=env.gates, consume=forbidden)

    # Then
    assert result.verdict == HOLD
    assert _pending(tmp_path).read_bytes() == before


def test_unreadable_record_is_held_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    env = _install(tmp_path, monkeypatch)
    _pending(tmp_path).parent.mkdir(mode=0o700, parents=True)
    _ = _pending(tmp_path).write_bytes(b"{not json")

    # When
    result = reconcile(_request(), gate_dir=skill_gate.GATE_DIR, store_root=_store(tmp_path), gate_for=env.gates)

    # Then
    assert result.verdict == HOLD
    assert _pending(tmp_path).read_bytes() == b"{not json"


def test_probe_surface_error_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    env = _approved(tmp_path, monkeypatch)
    store = _store(tmp_path)

    def fail_probe(
        _gate: skill_gate_approval.SkillApprovalGate, _request: ApprovalRequest
    ) -> Probe:
        raise ApprovalSurfaceError("discord unavailable")

    monkeypatch.setattr(skill_gate_approval.SkillApprovalGate, "probe", fail_probe)

    # When
    result = reconcile(_request(), gate_dir=skill_gate.GATE_DIR, store_root=store, gate_for=env.gates)

    # Then
    assert result.verdict == HOLD


def test_deleted_message_after_live_check_is_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the digest is live, but its approval message disappears before reconciliation.
    env = _approved(tmp_path, monkeypatch)
    store = _store(tmp_path)
    monkeypatch.setattr(
        skill_gate_approval.SkillApprovalGate,
        "probe",
        lambda _gate, _request: Probe.MISSING,
    )

    # When: reconciliation classifies the now-unreachable approval.
    result = reconcile(
        _request(),
        gate_dir=skill_gate.GATE_DIR,
        store_root=store,
        gate_for=env.gates,
    )

    # Then: deletion is terminal, not a transient hold that retries forever.
    assert result == Reconciled(SETTLED, "approval-message-missing")


def test_held_consume_lease_keeps_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    env = _approved(tmp_path, monkeypatch)
    before = _pending(tmp_path).read_bytes()
    store = _store(tmp_path)
    lease_root = skill_gate.GATE_DIR / skill_gate_request.LEASE_DIRNAME
    lease_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = (lease_root / f"skill-deploy%3a{_SKILL}.lease").open("a", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    # When
    try:
        result = reconcile(_request(), gate_dir=skill_gate.GATE_DIR, store_root=store, gate_for=env.gates)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    # Then
    assert result.verdict == HOLD
    assert _pending(tmp_path).read_bytes() == before
