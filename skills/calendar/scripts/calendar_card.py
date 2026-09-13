"""Prepare calendar card bytes before resolving a request's Discord surface."""
from __future__ import annotations

from datetime import UTC, datetime

import calendar_confirm
import calendar_gate
from calendar_confirm_input import DraftRecord


def prepare_draft(draft: DraftRecord) -> DraftRecord:
    from automation.interop.approval_card import CardRenderError, prepare

    version = draft.get("render_version", "1" if draft.get("approval_thread_id") else None)
    try:
        card = prepare(lambda selected: calendar_confirm.render_confirmation(
            {**draft, "render_version": selected},
        ), version)
    except CardRenderError as error:
        raise calendar_gate.GateError(str(error), 3) from error
    return {**draft, "render_version": card.render_version, "approval_content": card.content}


def draft_created(value: str) -> datetime:
    created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise calendar_gate.GateError("드래프트 created UTC 누락", 3)
    return created.astimezone(UTC)
