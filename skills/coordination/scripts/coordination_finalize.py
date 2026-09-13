

"""Coordination owner-input execution, with unchanged lifecycle facade seams."""
from __future__ import annotations
import argparse
import sys
from importlib import import_module

def finalize(args: argparse.Namespace) -> int:
    """Finalize a text confirmation, or independently re-check its owner reaction."""
    lifecycle = import_module("coordination_lifecycle")
    lifecycle.io.ensure_runtime()
    lifecycle.io.calendar_scripts()
    config = lifecycle.io.interop_config()
    lifecycle._reject_cancel_reaction(args.draft, config["owner_id"])
    confirmed = lifecycle.io.run_calendar_cli(["confirm", "--draft", args.draft])
    if confirmed.returncode == 0:
        event_id = lifecycle.executed_event_id(confirmed.stdout)
    elif confirmed.returncode == 1:
        event_id = lifecycle._finalize_reaction(args.draft, config["owner_id"])
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
    entry = lifecycle._pending_entry(args.draft, required=False)
    return lifecycle.finish(
        config, args.correlation, commands, lifecycle.io.kst_label(args.slot, args.duration_min),
        args.summary, event_id,
        record=entry.origin_record() if entry is not None else {"id": args.draft},
    )

def _finalize_reaction(draft_id: str, owner_id: str) -> str:
    lifecycle = import_module("coordination_lifecycle")
    import calendar_gate

    entry = lifecycle._pending_entry(draft_id)
    if entry is None:
        raise lifecycle.io.CoordinationError("반응 확인용 pending confirm이 없습니다", 1)
    draft = calendar_gate.load_draft(draft_id)
    if draft.get("sha256") != entry.sha256:
        raise lifecycle.io.CoordinationError("pending confirm 드래프트 해시 불일치", 1)
    discord = lifecycle.DiscordApi(owner_id)
    if f"sha256:{entry.sha256}" not in discord.message_content(entry):
        raise lifecycle.io.CoordinationError("확정 DM 드래프트 해시 불일치", 1)
    action = lifecycle.reaction_action(entry, owner_id, discord)
    if action == lifecycle.CANCEL_EMOJI:
        raise lifecycle.io.CoordinationError("취소 반응이 있어 실행하지 않습니다", 1)
    if action != lifecycle.APPROVE_EMOJI:
        raise lifecycle.io.CoordinationError("소유자 확정 반응이 없습니다", 1)
    approval = calendar_gate.Approval(
        ref=f"reaction:{entry.dm_message_id}", method="owner_dm_reaction", owner=owner_id
    )
    return calendar_gate.execute_draft(draft, approval)
