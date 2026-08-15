"""이미 실현된 공급망 승인을 재실행하지 않고 기존 CAS로만 회수한다.

승인된 배포를 매 tick 다시 실행하면 이미 마운트된 효과에는 아무 이득이 없고,
Discord rate limit이 새 승인의 정상 실행까지 막는다. 반대로 live 저장소나 승인
바인딩을 확정할 수 없을 때 레코드를 지우면 복구 근거를 잃는다. 따라서 정확히
같은 digest가 live이고 같은 승인도 유효할 때만 기존 ``consume``에 회수를 맡긴다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.interop.approval_lifecycle import (
    ApprovalRecordsError,
    ApprovalRequest,
    ApprovalSurfaceError,
    Probe,
)
from automation.skill_gate_approval import SkillApprovalGate
from automation.skill_gate_retire import Retired, Retirement, consume as consume_record
from automation.supply_chain_plan import SETTLED, PendingRequest

RETIRE_DONE: Final = "retire-done"
RUN: Final = "run"
HOLD: Final = "hold"
_APPROVAL_HOLDS: Final = frozenset(
    {
        Probe.CANCELLED,
        Probe.BINDING_MISMATCH,
        Probe.BOUND_PENDING,
        Probe.UNVERIFIABLE,
    }
)
_RETIREMENT_HOLDS: Final = frozenset(set(Retirement) - {Retirement.CONSUMED})


@dataclass(frozen=True, slots=True)
class Reconciled:
    """재실행보다 보존이 안전한 상태를 호출자가 문자열 추측 없이 구분하게 한다."""

    verdict: str
    reason: str


def _hold(reason: str) -> Reconciled:
    return Reconciled(HOLD, reason)


def reconcile(
    request: PendingRequest,
    *,
    gate_dir: Path,
    store_root: Path,
    gate_for: Callable[[str, str], SkillApprovalGate],
    consume: Callable[[SkillApprovalGate, str], Retired] = consume_record,
) -> Reconciled:
    """확실한 미실현만 실행하고, 확실한 실현만 승인 바인딩 그대로 회수한다."""
    discovery = gate_for(request.name, "")
    expected_record = gate_dir / "pending" / f"{request.record_name}.json"
    if discovery.path() != expected_record:
        return _hold("record-path-mismatch")
    try:
        record = discovery.stored()
    except ApprovalRecordsError:
        return _hold("record-unreadable")
    if record is None:
        return _hold("record-missing")
    digest, message_id = record.get("hash", ""), record.get("message_id", "")
    if not digest or not message_id:
        return _hold("record-incomplete")

    gate = gate_for(request.name, digest)
    live = store_root / "live" / request.name
    if not live.is_symlink():
        return _hold("live-not-symlink") if live.exists() else Reconciled(RUN, "live-absent")
    try:
        live_target = live.readlink()
    except OSError:
        return _hold("live-unreadable")
    expected_target = store_root / "releases" / request.name / digest
    if live_target != expected_target:
        return Reconciled(RUN, "live-digest-mismatch")

    try:
        approval = ApprovalRequest(
            key=request.key,
            action_hash=record.get("action_hash", ""),
            message_id=message_id,
            channel_id=gate.channel_id(),
            created_at="",
        )
        decision = gate.probe(approval)
    except (ApprovalRecordsError, ApprovalSurfaceError, OSError):
        return _hold("approval-unreadable")
    if decision is Probe.MISSING:
        return Reconciled(SETTLED, "approval-message-missing")
    if decision in _APPROVAL_HOLDS:
        return _hold(f"approval-{decision.value}")
    if decision is not Probe.APPROVED:
        return _hold("approval-unclassifiable")

    retired = consume(gate, message_id)
    if retired.outcome in _RETIREMENT_HOLDS:
        return _hold(f"retirement-{retired.outcome.value}")
    if retired.outcome is Retirement.CONSUMED:
        return Reconciled(RETIRE_DONE, "already-realized")
    return _hold("retirement-unclassifiable")
