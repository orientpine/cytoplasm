"""Owner-confirm lifecycle for the coordination CLI, kept below CLI size limits."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import coordinate_io as io
from confirm_reaction_watch import APPROVE_EMOJI as APPROVE_EMOJI, CANCEL_EMOJI, DiscordApi, reaction_action
from coordination_pending import PendingConfirm, PendingConfirmError, PendingConfirmStore
from coordination_card import OwnerCardDraft
from coordination_finalize import finalize as finalize, _finalize_reaction as _finalize_reaction

E2E_DM_PREFIX = "[E2E] "
#: Terminal states a result notice may close its request thread with (origin_notice 소유).
OUTCOME_DONE = "done"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_EXPIRED = "expired"


def owner_leg(args: argparse.Namespace, config: dict[str, str], correlation: str, state, slot_iso: str) -> int:
    """Create the gated calendar draft and send a reaction-ready owner request."""
    import calendar_core
    import calendar_gate

    from automation.interop import coordination

    start = datetime.fromisoformat(slot_iso)
    request = calendar_core.ParsedRequest(
        summary=args.summary, start=start, end=start + timedelta(minutes=args.duration_min)
    )
    draft: OwnerCardDraft = calendar_gate.build_draft(
        action="create", argv=calendar_core.build_create_argv(args.calendar, request),
        calendar_id=args.calendar, event_id="", summary=args.summary,
        start=request.start.isoformat(), end=request.end.isoformat(), channel_id="dm",
    )
    label = io.kst_label(slot_iso, args.duration_min)
    if not args.e2e_confirm:
        import coordination_approval
        from coordination_card import legacy
        from automation.interop.approval_card import CardRenderError, prepare

        def prepare_payload() -> coordination_approval.CoordinationApprovalPayload:
            def render(version: str) -> str:
                selected = draft.copy()
                selected["render_version"] = version
                return render_owner_card(selected, legacy(draft, args, correlation))

            try:
                card = prepare(render, draft.get("render_version"))
            except CardRenderError as error:
                raise io.CoordinationError(str(error), 3) from error
            prepared = draft.copy()
            prepared["render_version"] = card.render_version
            calendar_gate.persist_draft(prepared)
            io.obs(step="draft", draft_id=draft["id"], slot=slot_iso)
            return coordination_approval.CoordinationApprovalPayload(
                draft=prepared,
                slot=slot_iso,
                summary=args.summary,
                correlation=correlation,
                duration_min=args.duration_min,
                content=card.content,
                origin_channel_id=args.origin_channel_id,
                origin_message_id=args.origin_message_id,
            )

        entry = coordination_approval.request_confirmation(
            coordination_approval.CoordinationApprovalPayload(
                draft, slot_iso, args.summary, correlation, args.duration_min, "",
                args.origin_channel_id, args.origin_message_id,
            ), config["owner_id"], prepare=prepare_payload,
        )
        print(
            f"PENDING-OWNER draft={entry.draft_id} slot={slot_iso} correlation={correlation}"
        )
        return 7
    calendar_gate.persist_draft(draft)
    io.obs(step="draft", draft_id=draft["id"], slot=slot_iso)
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
    return finish(
        config, correlation, commands, label, args.summary, event_id,
        record={
            "id": draft["id"],
            "origin_channel_id": args.origin_channel_id,
            "origin_message_id": args.origin_message_id,
        },
    )


def render_owner_card(draft: OwnerCardDraft, legacy: str) -> str:
    """Replay v1/v2 bytes; v3 gives already-public facts to owner-ko-v2 on separate lines."""
    version = draft.get("render_version", "1")
    if version == "1":
        return legacy
    from automation.interop.approval_card import CardRenderError
    if version not in ("2", "3"):
        raise CardRenderError("unknown coordination card render version")
    from automation.interop import owner_message
    from coordination_binding import reaction_instruction
    from confirm_reaction_watch import EXPIRY
    if not callable(getattr(owner_message, "render", None)):
        raise CardRenderError("owner envelope unavailable")
    here = owner_message.Ref(scope="self")
    envelope = owner_message.OwnerMessage(
        subject_key=str(draft["id"]), subject="일정 조율",
        fact=("\n" if version == "3" else " · ").join(legacy.splitlines()[:2]), location=here,
        owner=owner_message.Action("react", here, reaction_instruction()),
        agent_next="승인된 일정을 캘린더에 등록", recovery="not_applicable",
        detail=owner_message.Approval(datetime.fromisoformat(draft["created"]) + EXPIRY, "일정을 등록하지 않음"),
        render_version="owner-ko-v2" if version == "3" else "owner-ko-v1",
    )
    try:
        body = owner_message.render(envelope, destination=here)
    except owner_message.OwnerMessageError as error:
        raise CardRenderError("coordination envelope cannot render") from error
    return f"{body}\nsha256:{draft['sha256']}"


def finish(
    config: dict[str, str], correlation: str, commands, label: str, summary: str, event_id: str,
    *, record: dict[str, str] | None = None,
) -> int:
    """Send only the established terse team notice and the routed result notice."""
    from automation.interop import coordination

    for command in commands:
        if command.kind == "post_team_confirmation":
            team_message = io.post_message(
                io.team_channel_id(), coordination.team_notice(correlation, label)
            )
            io.obs(step="team_notice", message_suffix=team_message[-4:])
        elif command.kind == "notify_result":
            _notify_completion(config, correlation, label, summary, record or {})
    print(f"EXECUTED correlation={correlation} event={event_id[:6]}…")
    return 0


def _notify_completion(
    config: dict[str, str], correlation: str, label: str, summary: str, record: dict[str, str]
) -> None:
    """Best-effort result notice — the calendar write is already committed."""
    try:
        notify_result(
            {**record, "summary": summary},
            f"✅ 일정 조율 완료 ({correlation}): {summary} — {label}. 캘린더에 등록되었습니다.",
            fallback=lambda content: send_owner_dm(config["owner_id"], content),
            outcome=OUTCOME_DONE,
        )
    except Exception as error:  # noqa: BLE001 — a notice failure must not change the exit code
        print(f"NOTIFY-FAIL correlation={correlation} err={type(error).__name__}", file=sys.stderr)


def _origin_notice() -> Any:
    io.ensure_runtime()
    from automation.interop import origin_notice  # noqa: PLC0415

    return origin_notice


def _thread_transport(channel_id: str) -> Any:
    io.ensure_runtime()
    from automation.interop.discord_transport import DiscordTransport  # noqa: PLC0415

    return DiscordTransport(token=io.discord_bot_token(), channel_id=channel_id)


def notify_result(
    pending_or_draft: dict[str, str],
    content: str,
    *,
    fallback: Callable[[str], object],
    outcome: str = "",
) -> object:
    """Route a coordination result: this request's approval thread, else the fallback.

    공유 deliver가 목적지별 렌더·폴백·종결 표시·실패 마커를 소유한다.
    제목 없는 스레드 이름과 호출자의 폴백 표면은 그대로 유지한다.
    옛 런타임은 문자열 경로로 내린다. 공개 주입 인자는 호출부 호환 계약이다.
    """
    from coordination_result_notice import result_message

    try:
        origin_notice = _origin_notice()
    except ImportError as error:  # 낡은 interop 런타임/샌드박스 — 결과는 그래도 소유자에게 닿아야 한다
        print(
            f"NOTIFY-HELPER-MISSING id={pending_or_draft.get('id', '')} err={type(error).__name__}",
            file=sys.stderr,
        )
        return fallback(content)
    message = result_message(pending_or_draft, content, outcome)
    if message is not None and getattr(origin_notice, "ACCEPTS_OWNER_MESSAGE", False):
        return origin_notice.deliver(
            api=io.api, transport_factory=_thread_transport, record=pending_or_draft,
            thread_name=f"일정 조율 결과 (draft {pending_or_draft.get('id', '')})",
            content=content, fallback=fallback,
            outcome=origin_notice.ThreadOutcome[outcome.upper()] if outcome else None,
            message=message, fallback_destination=None,
        )
    return origin_notice.deliver(
        api=io.api,
        transport_factory=_thread_transport,
        record=pending_or_draft,
        thread_name=f"일정 조율 결과 (draft {pending_or_draft.get('id', '')})",
        content=content,
        fallback=fallback,
        outcome=origin_notice.ThreadOutcome[outcome.upper()] if outcome else None,
    )


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
