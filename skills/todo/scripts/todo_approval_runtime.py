"""Runtime dependency assembly for the todo approval adapter."""
from __future__ import annotations

import importlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from todo_approval import (
    ApprovalRuntime,
    TodoApprovalError,
    TodoApprovalIntent,
    _repo_module,
    lifecycle,
    request_approval,
    surface_module,
)
from todo_approval_store import TodoApprovalStore

if TYPE_CHECKING:
    from automation.interop.approval_lifecycle import Verdict


def request_cli_approval(intent: TodoApprovalIntent, owner: str) -> Verdict:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token or not owner:
        raise TodoApprovalError("todo approval identity is unavailable")
    root = Path(os.environ.get("TODO_APPROVAL_ROOT", "~/.hermes/todo-approvals")).expanduser()
    transport = importlib.import_module("todo_discord").TodoDiscordTransport(token)
    directory_module = _repo_module("approval_directory")
    directory = directory_module.DiscordChannelDirectory(
        token,
        owner,
        transport.api,
        root / "approval-directory.json",
    )
    surface = surface_module()
    binding = surface.resolve_new_binding(surface.ApprovalKind.TODO, directory, owner)
    lease_module = _repo_module("approval_lease")
    runtime = ApprovalRuntime(
        TodoApprovalStore(root),
        transport,
        directory,
        owner,
        binding,
        lease_module.FileKeyLease(root / "approval-leases"),
        lease_module.PostingJournal(root / "posting-journal"),
        lambda: datetime.now(UTC),
    )
    verdict = request_approval(intent, runtime)
    if verdict.outcome not in {lifecycle().Outcome.POSTED, lifecycle().Outcome.PENDING}:
        reason = "unknown" if verdict.reason is None else verdict.reason.value
        raise TodoApprovalError(f"todo approval request was not posted: {reason}")
    return verdict


def origin_record(action_hash: str) -> dict[str, str] | None:
    """Routing facts of the latest approval generation for this exact argv hash."""
    root = Path(os.environ.get("TODO_APPROVAL_ROOT", "~/.hermes/todo-approvals")).expanduser()
    store = TodoApprovalStore(root)
    key = f"todo:{action_hash}"
    candidates = [record for record in (store.active(key), *store.archives(key)) if record is not None]
    if not candidates:
        return None
    latest = max(candidates, key=lambda record: record.generation)
    return {
        "id": action_hash[:19],
        "channel_id": latest.channel_id,
        "origin_channel_id": latest.origin_channel_id,
        "origin_message_id": latest.origin_message_id,
    }


def notify_result(
    record_like: dict,
    content: str,
    *,
    thread_name: str,
    transport: object | None = None,
    transport_factory: object | None = None,
) -> object:
    """Result notice: origin-channel thread first, the record's stored channel as fallback.

    라우팅·폴백·NOTIFY-THREAD-FAIL 의미는 공유 `automation.interop.origin_notice.deliver`가
    소유한다(2026-08-23 전 스킬 공통화). 폴백 목적지는 새로 해석하지 않고 레코드에
    저장된 승인 채널을 그대로 쓴다(워처의 stored-binding 규약).
    """
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if transport is None:
        if not token:
            raise TodoApprovalError("todo notice identity is unavailable")
        transport = importlib.import_module("todo_discord").TodoDiscordTransport(token)
    if transport_factory is None:
        discord_transport = _repo_module("discord_transport")

        def transport_factory(channel_id: str):
            return discord_transport.DiscordTransport(token=token, channel_id=channel_id)

    fallback_channel = str(record_like.get("channel_id") or "")
    if not fallback_channel:
        raise TodoApprovalError("todo notice has no stored channel to fall back to")
    try:
        origin_notice = _repo_module("origin_notice")
    except (ImportError, TodoApprovalError) as error:  # 낡은 interop 런타임/샌드박스
        print(
            f"NOTIFY-HELPER-MISSING id={record_like.get('id', '')} err={type(error).__name__}",
            file=sys.stderr,
        )
        return transport.post_message(fallback_channel, content)  # type: ignore[attr-defined]
    return origin_notice.deliver(
        api=transport.api,  # type: ignore[attr-defined]
        transport_factory=transport_factory,  # type: ignore[arg-type]
        record=record_like,
        thread_name=thread_name,
        content=content,
        fallback=lambda body: transport.post_message(fallback_channel, body),  # type: ignore[attr-defined]
    )
