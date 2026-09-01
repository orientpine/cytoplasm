#!/usr/bin/env python3
"""Agent-to-agent scheduling coordination CLI (W3-3).

Flow: §2 availability query to the peer → intersect with my calendar →
up to 3 candidates → peer slot approval (§2) → owner (cha) approval through
the W3-1 gated calendar skill → gws insert → terse #team confirmation +
cha DM result. §2 coordination envelopes flow on the interop channel
(#autophagy-agents); the terse confirmation notice still goes to #team.
Deadlock (no response within the timeout, or 0 candidates)
sends an escalation DM and terminates with ZERO calendar writes. A refusal
allows exactly one renegotiation round, then terminates with zero writes.

Exit codes: 0 executed | 2 input rejected | 3 config/env error
            4 deadlock (escalation DM sent, 0 writes)
            5 refusal terminated (0 writes) | 7 pending owner confirm
Production timeout: 600 s (10 min). Tests inject --timeout-s.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys

import coordinate_io as io
from coordination_lifecycle import finalize as lifecycle_finalize, owner_leg, reason_ko, send_owner_dm
from coordination_time import (
    RequestRangeInput,
    current_kst,
    resolve_request_range,
)

EXIT_DEADLOCK = 4
EXIT_REFUSED = 5
EXIT_PENDING_OWNER = 7


def _reject_calendar_intent(args: argparse.Namespace, request_range) -> None:
    """Refuse calendar-intent (an exact single slot) before any peer network I/O.

    Reciprocal to calendar's ROUTING-REJECT: when the owner fixed one exact
    start time (the resolved window is exactly ``duration_min`` long) and gave no
    negotiate cue, this is a solo calendar event whose title merely names a peer
    — NOT a negotiation. Post-incident 2026-07-20: a fixed slot must never fan
    out to a peer availability query that could drift to another day/time.
    Fail-closed with exit 2; the caller re-routes to the calendar skill.
    """
    window_minutes = (request_range.end - request_range.start).total_seconds() / 60
    is_exact_slot = window_minutes <= args.duration_min
    cue_text = f"{args.summary}\n{args.when or ''}"
    import calendar_routing

    if is_exact_slot and not calendar_routing.has_coordination_cue(cue_text):
        raise io.CoordinationError(
            "ROUTING-REJECT 정확한 단일 시각이 지정된 요청입니다 — 본인 단독 일정은 "
            "calendar 스킬로 등록하세요 (coordination은 시간 조율이 필요한 범위 요청 전용). "
            "조율이 필요하면 범위(예: '오전')로 요청하거나 '조율' 의사를 명시하세요.",
            2,
        )


def cmd_request(args: argparse.Namespace) -> int:
    io.ensure_runtime()
    io.calendar_scripts()
    from automation.interop import coordination
    from automation.interop.delegation import InteropEnvelope, format_envelope

    if args.peer_decline and os.environ.get("E2E_TEST_MODE") != "1":
        raise io.CoordinationError("--peer-decline은 E2E_TEST_MODE=1 전용입니다", 2)
    if args.e2e_confirm and os.environ.get("E2E_TEST_MODE") != "1":
        raise io.CoordinationError("--e2e-confirm은 E2E_TEST_MODE=1 전용입니다", 2)
    request_range = resolve_request_range(
        RequestRangeInput(args.when, args.range_start, args.range_end), current_kst()
    )
    if request_range.end <= request_range.start or args.duration_min <= 0:
        raise io.CoordinationError("범위/길이가 올바르지 않습니다", 2)
    _reject_calendar_intent(args, request_range)

    config = io.interop_config()
    channel = io.interop_channel_id()
    correlation = f"{coordination.CORRELATION_PREFIX}{secrets.token_hex(6)}"
    timeout_s = float(args.timeout_s)
    state, commands = coordination.start()
    io.obs(step="start", correlation=correlation, timeout_s=timeout_s)

    while commands:
        command, commands = commands[0], commands[1:]
        if command.kind == "send_availability_query":
            io.post_message(channel, format_envelope(InteropEnvelope(
                correlation, config["agent_id"], args.peer, coordination.QUERY_AVAILABILITY,
                {"range_start": request_range.start.isoformat(), "range_end": request_range.end.isoformat(),
                 "duration_min": args.duration_min},
            )))
            response = io.poll_envelope(
                channel_id=channel, correlation_id=correlation,
                intent=coordination.RESPONSE_AVAILABILITY, sender_id=args.peer,
                timeout_s=timeout_s,
            )
            if response is None:
                state, commands = coordination.on_timeout(state)
                continue
            peer_slots = coordination.availability_slots(response.payload)
            busy = coordination.parse_busy_intervals(
                io.busy_items(calendar_id=args.calendar, range_start=request_range.start.isoformat(),
                              range_end=request_range.end.isoformat()))
            candidates = coordination.candidate_slots(
                peer_slots=peer_slots, busy=busy, range_start=request_range.start,
                range_end=request_range.end, duration_min=args.duration_min)
            io.obs(step="candidates", peer_slots=len(peer_slots), busy=len(busy),
                   candidates=list(candidates))
            state, commands = coordination.on_availability(state, candidates)
        elif command.kind == "send_slot_confirm":
            payload: dict = {"slot": command.slot, "duration_min": args.duration_min}
            if args.peer_decline:
                payload["simulate"] = "decline"
            io.post_message(channel, format_envelope(InteropEnvelope(
                correlation, config["agent_id"], args.peer,
                coordination.QUERY_CONFIRM_SLOT, payload,
            )))
            response = io.poll_envelope(
                channel_id=channel, correlation_id=correlation,
                intent=coordination.RESPONSE_CONFIRM_SLOT, sender_id=args.peer,
                timeout_s=timeout_s, payload_slot=command.slot,
            )
            if response is None:
                state, commands = coordination.on_timeout(state)
                continue
            accepted = coordination.confirm_slot_accepted(response.payload)
            io.obs(step="peer_confirm", slot=command.slot, accepted=accepted)
            state, commands = coordination.on_peer_confirm(state, accepted)
        elif command.kind == "request_owner_confirm":
            return owner_leg(args, config, correlation, state, command.slot)
        elif command.kind == "notify_escalation":
            send_owner_dm(config["owner_id"],
                f"⚠️ 일정 조율 에스컬레이션 ({correlation}): "
                f"{reason_ko(command.reason)} — 인간 협의가 필요합니다. 캘린더 변경 0건.")
            io.obs(step="escalation", reason=command.reason, phase=state.phase.value)
            print(f"DEADLOCK reason={command.reason} correlation={correlation}")
            return EXIT_DEADLOCK
        elif command.kind == "notify_termination":
            send_owner_dm(config["owner_id"],
                f"🚫 일정 조율 종료 ({correlation}): {reason_ko(command.reason)} — "
                f"재협상 1회 후 종료했습니다. 캘린더 변경 0건.")
            io.obs(step="termination", reason=command.reason)
            print(f"REFUSED reason={command.reason} correlation={correlation}")
            return EXIT_REFUSED
        else:  # pragma: no cover - state machine never emits others here
            raise io.CoordinationError(f"예상 밖 커맨드: {command.kind}", 3)
    raise io.CoordinationError("커맨드 루프가 결론 없이 종료됨", 3)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coordinate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request", help="조율 시작 (양측 승인 전 캘린더 무변경)")
    request.add_argument("--peer", required=True)
    request.add_argument("--summary", required=True)
    request.add_argument("--when", help="KST 자연어 날짜/시간대 (예: 내일 오후)")
    request.add_argument("--range-start", help="명시적 ISO 시작 시각 (+09:00 포함)")
    request.add_argument("--range-end", help="명시적 ISO 종료 시각 (+09:00 포함)")
    request.add_argument("--duration-min", type=int, default=30)
    request.add_argument("--calendar", default="primary")
    request.add_argument("--timeout-s", type=float, default=600.0,
                         help="무응답 데드락 타임아웃 (프로덕션 600s=10분)")
    request.add_argument("--origin-channel-id", default="",
                         help="지시가 온 채널 id (결과를 그 채널 스레드로 통지)")
    request.add_argument("--origin-message-id", default="",
                         help="지시 메시지 id (결과 스레드를 그 메시지에 건다)")
    request.add_argument("--e2e-confirm", action="store_true")
    request.add_argument("--peer-decline", action="store_true")
    request.set_defaults(func=cmd_request)
    finalize = sub.add_parser("finalize", help="cha의 `실행 <draft-id>` 답장 후 실행+통지")
    finalize.add_argument("--draft", required=True)
    finalize.add_argument("--slot", required=True)
    finalize.add_argument("--summary", required=True)
    finalize.add_argument("--duration-min", type=int, default=30)
    finalize.add_argument("--correlation", default="coord-manual")
    finalize.set_defaults(func=lifecycle_finalize)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except io.CoordinationError as error:
        print(f"COORD-REFUSED {error}", file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
