"""Owner-confirm lifecycle for the coordination CLI, kept below CLI size limits."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import coordinate_io as io
from confirm_reaction_watch import APPROVE_EMOJI, CANCEL_EMOJI, DiscordApi, reaction_action
from coordination_pending import PendingConfirm, PendingConfirmError, PendingConfirmStore

E2E_DM_PREFIX = "[E2E] "


def owner_leg(args: argparse.Namespace, config: dict[str, str], correlation: str, state, slot_iso: str) -> int:
    """Create the gated calendar draft and send a reaction-ready owner request."""
    import calendar_core
    import calendar_gate

    from automation.interop import coordination

    start = datetime.fromisoformat(slot_iso)
    request = calendar_core.ParsedRequest(
        summary=args.summary, start=start, end=start + timedelta(minutes=args.duration_min)
    )
    draft = calendar_gate.create_draft(
        action="create", argv=calendar_core.build_create_argv(args.calendar, request),
        calendar_id=args.calendar, event_id="", summary=args.summary,
        start=request.start.isoformat(), end=request.end.isoformat(), channel_id="dm",
    )
    io.obs(step="draft", draft_id=draft["id"], slot=slot_iso)
    label = io.kst_label(slot_iso, args.duration_min)
    if not args.e2e_confirm:
        import coordination_approval
        import coordination_binding

        _ = coordination_approval.request_confirmation(
            coordination_approval.CoordinationApprovalPayload(
                draft=draft,
                slot=slot_iso,
                summary=args.summary,
                correlation=correlation,
                duration_min=args.duration_min,
                content=(
                    f"📅 일정 조율 ({correlation}): 상대 에이전트({args.peer})가 "
                    f"{label} 슬롯을 승인했습니다.\n제목: {args.summary}\n"
                    f"{coordination_binding.reaction_instruction()} — 또는 "
                    f"`실행 {draft['id']}`/`취소 {draft['id']}` 텍스트도 가능\n"
                    f"sha256:{draft['sha256']}"
                ),
            ),
            config["owner_id"],
        )
        print(
            f"PENDING-OWNER draft={draft['id']} slot={slot_iso} correlation={correlation}"
        )
        return 7
    with tempfile.TemporaryDirectory() as tmp:
        injection = Path(tmp) / "confirm.json"
        signed = io.run_calendar_cli(["sign", "--draft", draft["id"], "--out", str(injection)])
        if signed.returncode != 0:
            raise io.CoordinationError(f"sign 실패: {signed.stderr.strip()[:200]}", 3)
        confirmed = io.run_calendar_cli(
            ["confirm", "--draft", draft["id"], "--injection-file", str(injection)]
        )
    if confirmed.returncode != 0:
        io.obs(step="owner_confirm", accepted=False, rc=confirmed.returncode)
        raise io.CoordinationError(
            f"confirm 실패: {confirmed.stderr.strip()[:200]}", confirmed.returncode or 3
        )
    state, commands = coordination.on_owner_confirm(state, True)
    event_id = executed_event_id(confirmed.stdout)
    io.obs(step="executed", draft_id=draft["id"], event_prefix=event_id[:6])
    state, commands = coordination.on_executed(state)
    return finish(config, correlation, commands, label, args.summary, event_id)


def finalize(args: argparse.Namespace) -> int:
    """Finalize a text confirmation, or independently re-check its owner reaction."""
    io.ensure_runtime()
    io.calendar_scripts()
    config = io.interop_config()
    _reject_cancel_reaction(args.draft, config["owner_id"])
    confirmed = io.run_calendar_cli(["confirm", "--draft", args.draft])
    if confirmed.returncode == 0:
        event_id = executed_event_id(confirmed.stdout)
    elif confirmed.returncode == 1:
        event_id = _finalize_reaction(args.draft, config["owner_id"])
    else:
        print(confirmed.stderr.strip(), file=sys.stderr)
        return confirmed.returncode
    from automation.interop import coordination

    state, _ = coordination.on_owner_confirm(
        coordination.CoordinationState(
            phase=coordination.Phase.AWAIT_OWNER_CONFIRM, candidates=(args.slot,)
        ),
        True,
    )
    _, commands = coordination.on_executed(state)
    return finish(
        config, args.correlation, commands, io.kst_label(args.slot, args.duration_min),
        args.summary, event_id,
    )


def finish(config: dict[str, str], correlation: str, commands, label: str, summary: str, event_id: str) -> int:
    """Send only the established terse team notice and owner result DM."""
    from automation.interop import coordination

    for command in commands:
        if command.kind == "post_team_confirmation":
            team_message = io.post_message(
                io.team_channel_id(), coordination.team_notice(correlation, label)
            )
            io.obs(step="team_notice", message_suffix=team_message[-4:])
        elif command.kind == "notify_result":
            send_owner_dm(
                config["owner_id"],
                f"✅ 일정 조율 완료 ({correlation}): {summary} — {label}. 캘린더에 등록되었습니다.",
            )
    print(f"EXECUTED correlation={correlation} event={event_id[:6]}…")
    return 0


def send_owner_dm(owner_id: str, content: str) -> tuple[str, str]:
    """Post a DM (E2E runs are prefixed) and return its channel/message identity."""
    if os.environ.get("E2E_TEST_MODE") == "1":
        content = E2E_DM_PREFIX + content
    channel_id = io.owner_approval_channel(owner_id)
    return channel_id, io.post_message(channel_id, content)


def reason_ko(reason: str) -> str:
    labels = {
        "peer_timeout": "상대 에이전트 무응답(타임아웃)",
        "no_candidates": "공통 가용 후보 0개",
        "declined_by_peer": "상대측 거절",
        "declined_by_owner": "소유자 거절",
    }
    return labels.get(reason, reason)


def executed_event_id(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("EXECUTED "):
            for token in line.split():
                if token.startswith("event="):
                    return token.removeprefix("event=")
    return ""


def _finalize_reaction(draft_id: str, owner_id: str) -> str:
    import calendar_gate

    entry = _pending_entry(draft_id)
    if entry is None:
        raise io.CoordinationError("반응 확인용 pending confirm이 없습니다", 1)
    draft = calendar_gate.load_draft(draft_id)
    if draft.get("sha256") != entry.sha256:
        raise io.CoordinationError("pending confirm 드래프트 해시 불일치", 1)
    discord = DiscordApi(owner_id)
    if f"sha256:{entry.sha256}" not in discord.message_content(entry):
        raise io.CoordinationError("확정 DM 드래프트 해시 불일치", 1)
    action = reaction_action(entry, owner_id, discord)
    if action == CANCEL_EMOJI:
        raise io.CoordinationError("취소 반응이 있어 실행하지 않습니다", 1)
    if action != APPROVE_EMOJI:
        raise io.CoordinationError("소유자 확정 반응이 없습니다", 1)
    approval = calendar_gate.Approval(
        ref=f"reaction:{entry.dm_message_id}", method="owner_dm_reaction", owner=owner_id
    )
    return calendar_gate.execute_draft(draft, approval)


def _reject_cancel_reaction(draft_id: str, owner_id: str) -> None:
    entry = _pending_entry(draft_id, required=False)
    if entry is None:
        return
    discord = DiscordApi(owner_id)
    if f"sha256:{entry.sha256}" not in discord.message_content(entry):
        raise io.CoordinationError("확정 DM 드래프트 해시 불일치", 1)
    if reaction_action(entry, owner_id, discord) == CANCEL_EMOJI:
        raise io.CoordinationError("취소 반응이 있어 실행하지 않습니다", 1)


def _pending_entry(draft_id: str, *, required: bool = True) -> PendingConfirm | None:
    try:
        entries = [entry for entry in PendingConfirmStore().load() if entry.draft_id == draft_id]
    except PendingConfirmError as error:
        raise io.CoordinationError("pending confirm store를 신뢰할 수 없습니다", 3) from error
    if len(entries) == 1:
        return entries[0]
    if not required and not entries:
        return None
    raise io.CoordinationError("반응 확인용 pending confirm이 유일하지 않습니다", 1)
