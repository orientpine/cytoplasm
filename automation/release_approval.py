"""Release approval CLI over the shared skill gate and lifecycle (VA-1).

One owner ✅ binds one version, HEAD and surface set; the nonce is outside the hash.
Decision exits: 0 approved · 9 denied · 7 pending/unverifiable · 2 absent/other HEAD.
"""
# 새 승인 기계장치는 하나도 만들지 않는다 — 스펙(`ReleaseSpec`)·게이트(`SkillApprovalGate`)·
# lifecycle 호스트(`skill_gate_request`)를 그대로 재사용하고, 표면은 `ApprovalKind.RELEASE`
# 가 선언한다(§10-2 — 2차 주체인 peer 봇이 같은 채널을 읽는다).
# action hash 는 무작위 nonce 를 제외한다. 그래서 같은 plan 으로 명령을 다시 돌리면 살아 있는
# 요청을 고아로 만들지 않고 **재개**한다(승인 메시지 단일성 규칙).
from __future__ import annotations

import argparse
import json
import secrets
import sys
from dataclasses import replace
from pathlib import Path
from typing import Final, assert_never

from automation import (
    release_abandon,
    release_notes,
    release_plan,
    skill_gate,
    skill_gate_request,
    skill_gate_surface,
)
from automation.interop.approval_lifecycle import (
    ApprovalRecordsError,
    ApprovalSurfaceError,
    Probe,
)
from automation.interop.approval_surface import ApprovalKind
from automation.interop.discord_transport import DiscordTransport
from automation.release_card import card_for_new_request
from automation.release_spec import ReleaseSpec, ReleaseSpecError, spec_from_plan, spec_from_record
from automation.release_retire import notify_stale_approval
from automation.skill_gate_approval import GateSurface, SkillApprovalGate

#: release.sh 의 자동 복구가 읽는 유일한 기계 판독 줄 — 거절 메시지는 바뀌지 않는다.
STALE_PENDING_PREFIX: Final = "RELEASE-REQUEST-STALE:"
DECISION_APPROVED: Final = 0
DECISION_UNAVAILABLE: Final = 2
DECISION_PENDING: Final = 7
DECISION_DENIED: Final = 9


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
    try:
        plan = release_plan.build_plan(
            Path(args.repo),
            base=args.base,
            head=args.head,
            version=args.version,
            bump=args.bump,
        )
    except release_plan.ReleasePlanError as error:
        print(f"RELEASE-PLAN-BLOCK: {error}", file=sys.stderr)
        return 4
    payload = {
        "base": plan.base,
        "head": plan.head,
        "major_note": release_notes.major_note(plan),
        "patch_notes": release_notes.detail_body(plan),
        "surface_digests": [list(row) for row in plan.surface_digests],
        "version": plan.version,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_retire(args: argparse.Namespace) -> int:
    return release_abandon.retire_previous(str(args.head), str(getattr(args, "tip", "")), _gate)


def _stale_pending_line() -> str | None:
    """Blocking record identity with its own verified probe; uncertainty yields no hint."""
    # `binding-mismatch` 거절은 '다른 요청이 살아 있다'까지만 말한다. 그 요청이 소유자 결정을
    # 기다리는 중인지, 어떤 버전·HEAD 에 묶여 있는지는 레코드 자신의 스펙으로 다시 프로브해야만
    # 알 수 있다(`cmd_decision` 과 같은 복원 경로, 사본 0). 확인에 실패하면 아무 줄도 내지
    # 않는다 — 불확실은 자동 복구의 근거가 될 수 없다.
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


def detail_transport(channel_id: str) -> DiscordTransport:
    """변경 상세를 보내는 유일한 이음매 — 자격증명은 게이트가 읽는 그 토큰 하나다."""
    return DiscordTransport(
        token=skill_gate._token(),  # noqa: SLF001 - the gate owns the credential
        channel_id=channel_id,
    )


def _deliver_details(
    gate: SkillApprovalGate, spec: ReleaseSpec, record: dict[str, str]
) -> dict[str, str]:
    """카드 뒤에 변경 상세를 올리고 그 id 를 레코드에 남긴다 — 승인 바인딩 밖의 감사 흔적."""
    delivery = release_notes.post_details(
        detail_transport(gate.channel_id()).send, spec.detail_messages()
    )
    if delivery.failure:
        print(delivery.failure, file=sys.stderr)
    updated = {
        **record,
        "detail_message_ids": json.dumps(delivery.message_ids, separators=(",", ":")),
    }
    path = gate.path()
    _ = path.write_text(spec.serialize(updated), encoding="utf-8")
    path.chmod(0o600)
    return updated


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
    # 살아 있는 바인딩부터 해소한다 — 소유자가 이미 보고 있는 카드는 그 바이트에 결정이
    # 묶여 있으므로, 재사용 경로는 렌더러를 부르지 않는다(다시 그린 한 글자가 곧 거절이다).
    reused = skill_gate_request.reuse(_gate(spec))
    if reused is not None:
        return _emit_request(reused)
    # 재사용할 것이 없을 때에만 렌더한다. 최종 카드와 그 예산을 첫 효과(게시·레코드·저널)
    # 앞에서 확정하므로, 한도를 넘긴 요청은 고아 카드도 반쯤 만들어진 레코드도 남기지 않는다.
    card, refusal = card_for_new_request(spec)
    if card is None:
        refused = skill_gate_request.Requested(None, skill_gate_request.LIFECYCLE_REFUSAL_EXIT, refusal)
        return _emit_request(refused)
    gate = _gate(card)
    print(skill_gate_surface.where_to_look(ApprovalKind.RELEASE), file=sys.stderr)
    requested = skill_gate_request.post_request(gate, fresh=False)
    if requested.posted and requested.record is not None:
        requested = replace(requested, record=_deliver_details(gate, card, requested.record))
    return _emit_request(requested)


def cmd_abandon(args: argparse.Namespace) -> int:
    """Delegate audited abandonment through the existing remote producer surface."""
    # 로직은 한 줄도 복제하지 않는다 — `automation.release_abandon` 의 3필드 일치·fsync 감사·
    # 바이트 그대로의 archive 에 argv 로 위임한다. 워크스테이션에는 게이트 상태가 없으므로,
    # 이 서브커맨드가 없으면 자동 복구는 노드 셸을 열어야만 가능하다.
    return release_abandon.main([
        "--version", str(args.version), "--head", str(args.head),
        "--message-id", str(args.message_id), "--reason", str(args.reason),
    ])


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
    tagged_head = str(getattr(args, "tagged", "") or "")
    mismatched = bool(expected_head and record.get("head_sha", "") != expected_head)
    if mismatched and tagged_head and record.get("head_sha", "") == tagged_head:
        # 이 요청은 낡은 것이 아니라 **이미 실행된 릴리스**다 — 소유자 ✅ 를 받아 서명
        # 태그까지 잘렸고, 남은 일은 다음 release.sh 의 감사 회수뿐이다. 팁이 그 뒤로
        # 전진했다는 이유만으로 "자동 완결할 수 없습니다" 를 보내면 거짓말이 된다
        # (2026-09-10 실측: v1.6.7 의 적용 완료 통지와 ⛔ 가 나란히 도착했다).
        #
        # 인가 의미는 한 뼘도 넓히지 않는다 — rc 는 그대로 UNAVAILABLE 이라 옛 ✅ 로 새
        # 팁을 자르는 문은 잠긴 채다. Discord 도 다시 조회하지 않는다: 프로브 결과가
        # 무엇이든 답이 같으므로 조회는 예산만 쓴다.
        print(
            f"RELEASE-DECISION: executed release {tagged_head[:12]} awaiting retirement",
            file=sys.stderr,
        )
        return DECISION_UNAVAILABLE
    if mismatched and not getattr(args, "notify_stale", False):
        print("RELEASE-DECISION: live request is bound to a different HEAD", file=sys.stderr)
        return DECISION_UNAVAILABLE
    try:
        gate = _gate(spec_from_record(record))
        outstanding = gate.outstanding("release")
        if not outstanding:
            return DECISION_UNAVAILABLE
        probe = gate.probe(outstanding[0])
    except (ReleaseSpecError, ApprovalRecordsError, ApprovalSurfaceError, OSError) as error:
        print(f"RELEASE-DECISION: unverifiable ({type(error).__name__})", file=sys.stderr)
        return DECISION_UNAVAILABLE if mismatched else DECISION_PENDING
    if mismatched:
        print("RELEASE-DECISION: live request is bound to a different HEAD", file=sys.stderr)
        match probe:
            case Probe.APPROVED:
                notify_stale_approval(record, expected_head)
            case (
                Probe.BOUND_PENDING | Probe.CANCELLED | Probe.MISSING
                | Probe.BINDING_MISMATCH | Probe.UNVERIFIABLE
            ):
                return DECISION_UNAVAILABLE
            case unreachable:
                assert_never(unreachable)
        return DECISION_UNAVAILABLE
    print(f"RELEASE-DECISION: {probe.name.lower()} version={record['version']}", file=sys.stderr)
    return decision_exit(probe)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-approval")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="표면별 변경·패치노트 계획을 JSON 으로 출력")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--base", required=True)
    plan.add_argument("--head", required=True)
    plan.add_argument("--version", required=True)
    plan.add_argument("--bump", choices=("major", "minor", "patch"), default="patch")
    plan.set_defaults(run=cmd_plan)
    retire = commands.add_parser(
        "retire", help="서명·승인 완료된 이전 release를 감사 archive로 이동"
    )
    retire.add_argument("--head", required=True)
    retire.add_argument("--tip", default="", help="확인한 origin/main tip; 낡은 승인 자가 회수")
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
    decision.add_argument("--notify-stale", action="store_true")
    decision.add_argument(
        "--tagged",
        default="",
        help="origin/main 에서 도달 가능한 가장 가까운 릴리스 커밋 — 완결기가 실어 준다",
    )
    decision.set_defaults(run=cmd_decision)
    args = parser.parse_args(argv)
    return int(args.run(args))


if __name__ == "__main__":
    raise SystemExit(main())
