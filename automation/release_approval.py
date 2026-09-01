"""VA-1 release approval producer — one owner ✅ authorizes one whole release version.

No new approval machinery: the spec (`ReleaseSpec`), the gate (`SkillApprovalGate`) and
the lifecycle host (`skill_gate_request`) are reused as-is, and the surface is declared
by ``ApprovalKind.RELEASE`` (§10-2 — the second-party peer bot reads the same channel).
The action hash excludes the random nonce, so re-running the command for the same plan
RESUMES the live request instead of orphaning it (승인 메시지 단일성 규칙).

``decision`` exit codes: 0 approved · 9 denied (⛔ wins) · 7 pending/unverifiable ·
2 no live release request (or one bound to a different HEAD when ``--head`` is given).
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from automation import (
    release_abandon,
    release_plan,
    skill_gate,
    skill_gate_request,
    skill_gate_retire,
    skill_gate_surface,
)
from automation.interop.approval_lifecycle import (
    ApprovalRecordsError,
    ApprovalSurfaceError,
    Probe,
)
from automation.interop.approval_surface import ApprovalKind
from automation.release_spec import (
    ReleaseSpec,
    ReleaseSpecError,
    fit_patch_notes,
    spec_from_record,
)
from automation.release_retire import retire_released_record
from automation.skill_gate_approval import GateSurface, SkillApprovalGate

#: release.sh 의 자동 복구가 읽는 유일한 기계 판독 줄 — 거절 메시지는 바뀌지 않는다.
STALE_PENDING_PREFIX: Final = "RELEASE-REQUEST-STALE:"
DECISION_APPROVED: Final = 0
DECISION_UNAVAILABLE: Final = 2
DECISION_PENDING: Final = 7
DECISION_DENIED: Final = 9


def spec_from_plan(payload: Mapping[str, object], release_nonce: str) -> ReleaseSpec:
    """One immutable spec from the plan JSON `release.sh` carries between steps."""
    surfaces = payload.get("surface_digests")
    if not isinstance(surfaces, list):
        raise ReleaseSpecError("plan payload carries no surface digest list")
    return ReleaseSpec(
        version=str(payload.get("version", "")),
        head_sha=str(payload.get("head", "")),
        release_nonce=release_nonce,
        surface_digests=tuple((str(row[0]), str(row[1])) for row in surfaces),
        patch_notes=str(payload.get("patch_notes", "")),
    )


def decision_exit(probe: Probe) -> int:
    """⛔ wins; everything that is not a definite owner answer stays pending."""
    if probe is Probe.APPROVED:
        return DECISION_APPROVED
    if probe is Probe.CANCELLED:
        return DECISION_DENIED
    return DECISION_PENDING


def _bindings() -> skill_gate_surface.SupplyChainSurface:
    return skill_gate_surface.surface_for(
        ApprovalKind.RELEASE, skill_gate._identity()  # noqa: SLF001 - the gate owns this
    )


def _gate(spec: ReleaseSpec) -> SkillApprovalGate:
    surface = GateSurface(
        skill_gate._api,  # noqa: SLF001 - the gate owns this, deliberately
        skill_gate.GATE_DIR,
        skill_gate._owner_id,  # noqa: SLF001
        _bindings,
    )
    return SkillApprovalGate(surface, spec)


def _record_path() -> Path:
    return skill_gate.GATE_DIR / "pending" / "release.json"


def cmd_plan(args: argparse.Namespace) -> int:
    plan = release_plan.build_plan(
        Path(args.repo), base=args.base, head=args.head, version=args.version
    )
    patch_notes = fit_patch_notes(
        version=plan.version,
        head_sha=plan.head,
        surface_digests=plan.surface_digests,
        patch_notes=release_plan.render_patch_notes(plan),
    )
    payload = {
        "base": plan.base,
        "head": plan.head,
        "patch_notes": patch_notes,
        "surface_digests": [list(row) for row in plan.surface_digests],
        "version": plan.version,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_retire(args: argparse.Namespace) -> int:
    try:
        decoded = json.loads(_record_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("RELEASE-RETIRE: no previous release record", file=sys.stderr)
        return 0
    except (OSError, json.JSONDecodeError):
        print("RELEASE-RETIRE-BLOCK: release record is unreadable", file=sys.stderr)
        return 4
    if not isinstance(decoded, dict):
        return 4
    record = {str(name): str(value) for name, value in decoded.items()}
    try:
        gate = _gate(spec_from_record(record))
        outstanding = gate.outstanding("release")
        decision = gate.probe(outstanding[0]) if outstanding else Probe.UNVERIFIABLE
    except (
        ReleaseSpecError,
        ApprovalRecordsError,
        ApprovalSurfaceError,
        OSError,
    ) as error:
        print(f"RELEASE-RETIRE-BLOCK: {error}", file=sys.stderr)
        return 4
    if decision is not Probe.APPROVED:
        # 미결정·미확인 요청은 lifecycle 의 재사용/supersede 가 소유한다 — retire 가
        # 여기서 막으면 '재실행=재개' 계약이 깨진다(2026-08-31 v1.0.139 재개 실측).
        # retire 는 실행 완료(✅ + 서명 태그)된 승인만 감사 archive 로 옮긴다.
        print(
            "RELEASE-RETIRE: pending release is not an executed approval —"
            " leaving it to the request lifecycle",
            file=sys.stderr,
        )
        return 0
    try:
        archived = retire_released_record(
            _record_path(),
            skill_gate.GATE_DIR / "release-history",
            expected_head=args.head,
            decision=decision,
        )
    except (ReleaseSpecError, OSError) as error:
        print(f"RELEASE-RETIRE-BLOCK: {error}", file=sys.stderr)
        return 4
    print(
        f"RELEASE-RETIRE: archived {args.head[:12]}"
        if archived is not None
        else "RELEASE-RETIRE: no previous release record",
        file=sys.stderr,
    )
    return 0


def _stale_pending_line() -> str | None:
    """The blocking record's identity and OWN probe — or None when it cannot be read.

    ``binding-mismatch`` 거절은 '다른 요청이 살아 있다'까지만 말한다. 그 요청이 소유자
    결정을 기다리는 중인지, 어떤 버전·HEAD 에 묶여 있는지는 레코드 자신의 스펙으로
    다시 프로브해야만 알 수 있다(``cmd_decision`` 과 같은 복원 경로, 사본 0). 확인에
    실패하면 아무 줄도 내지 않는다 — 불확실은 자동 복구의 근거가 될 수 없다.
    """
    try:
        decoded = json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    record = {str(name): str(value) for name, value in decoded.items()}
    try:
        gate = _gate(spec_from_record(record))
        outstanding = gate.outstanding("release")
        if not outstanding:
            return None
        probe = gate.probe(outstanding[0])
    except (ReleaseSpecError, ApprovalRecordsError, ApprovalSurfaceError, OSError):
        return None
    return (
        f"{STALE_PENDING_PREFIX} version={record.get('version', '')}"
        f" head={record.get('head_sha', '')}"
        f" message_id={record.get('message_id', '')}"
        f" probe={probe.name.lower()}"
    )


def _emit_request(requested: skill_gate_request.Requested) -> int:
    """The legacy stdout/exit contract byte-for-byte, plus ONE stale hint on stderr."""
    exit_code = skill_gate_request.emit(requested, json_output=True)
    if exit_code == 0 or "reason=binding-mismatch" not in requested.message:
        return exit_code
    stale = _stale_pending_line()
    if stale is not None:
        print(stale, file=sys.stderr)
    return exit_code


def cmd_request(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReleaseSpecError("plan file is not a JSON object")
    spec = spec_from_plan(payload, secrets.token_hex(16))
    gate = _gate(spec)
    reused = skill_gate_request.reuse(gate)
    if reused is not None:
        return _emit_request(reused)
    print(skill_gate_surface.where_to_look(ApprovalKind.RELEASE), file=sys.stderr)
    return _emit_request(skill_gate_request.post_request(gate, fresh=False))


def cmd_abandon(args: argparse.Namespace) -> int:
    """The audited abandon, reachable through the SAME producer surface release.sh owns.

    로직은 한 줄도 복제하지 않는다 — ``automation.release_abandon`` 의 3필드 일치·fsync
    감사·바이트 그대로의 archive 를 그대로 위임한다. 워크스테이션에는 게이트 상태가
    없으므로, 이 서브커맨드가 없으면 자동 복구는 노드 셸을 열어야만 가능하다.
    """
    order = release_abandon.ReleaseAbandonOrder(
        version=str(args.version),
        head_sha=str(args.head),
        message_id=str(args.message_id),
        reason=str(args.reason),
        actor=skill_gate_retire.actor(),
    )
    return release_abandon.emit(
        release_abandon.abandon(
            skill_gate.GATE_DIR,
            order,
            skill_gate_retire.abandon_log(skill_gate.APPROVAL_LOG),
        )
    )


def cmd_decision(args: argparse.Namespace) -> int:
    try:
        decoded = json.loads(_record_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("RELEASE-DECISION: no live release request", file=sys.stderr)
        return DECISION_UNAVAILABLE
    except (OSError, json.JSONDecodeError):
        print("RELEASE-DECISION: release request record is unreadable", file=sys.stderr)
        return DECISION_UNAVAILABLE
    if not isinstance(decoded, dict):
        return DECISION_UNAVAILABLE
    record = {str(name): str(value) for name, value in decoded.items()}
    expected_head = str(getattr(args, "head", "") or "")
    if expected_head and record.get("head_sha", "") != expected_head:
        # An approval for another HEAD must never authorize this one (hash binding).
        print("RELEASE-DECISION: live request is bound to a different HEAD", file=sys.stderr)
        return DECISION_UNAVAILABLE
    try:
        gate = _gate(spec_from_record(record))
        outstanding = gate.outstanding("release")
        if not outstanding:
            return DECISION_UNAVAILABLE
        probe = gate.probe(outstanding[0])
    except (ReleaseSpecError, ApprovalRecordsError, ApprovalSurfaceError) as error:
        # Uncertainty is neither an approval nor a denial — the next poll looks again.
        print(f"RELEASE-DECISION: unverifiable ({type(error).__name__})", file=sys.stderr)
        return DECISION_PENDING
    print(f"RELEASE-DECISION: {probe.name.lower()}", file=sys.stderr)
    return decision_exit(probe)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-approval")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="표면별 변경·패치노트 계획을 JSON 으로 출력")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--base", required=True)
    plan.add_argument("--head", required=True)
    plan.add_argument("--version", required=True)
    plan.set_defaults(run=cmd_plan)
    retire = commands.add_parser(
        "retire", help="서명·승인 완료된 이전 release를 감사 archive로 이동"
    )
    retire.add_argument("--head", required=True)
    retire.set_defaults(run=cmd_retire)
    request = commands.add_parser("request", help="release 승인 요청 게시(재실행=재사용)")
    request.add_argument("--plan-file", required=True)
    request.set_defaults(run=cmd_request)
    abandon = commands.add_parser(
        "abandon", help="막고 있는 release 레코드를 감사와 함께 archive 로 놓아준다"
    )
    abandon.add_argument("--version", required=True)
    abandon.add_argument("--head", required=True)
    abandon.add_argument("--message-id", required=True)
    abandon.add_argument("--reason", required=True)
    abandon.set_defaults(run=cmd_abandon)
    decision = commands.add_parser("decision", help="소유자 결정 조회(0/9/7/2)")
    decision.add_argument("--head", default="")
    decision.set_defaults(run=cmd_decision)
    args = parser.parse_args(argv)
    return int(args.run(args))


if __name__ == "__main__":
    raise SystemExit(main())
