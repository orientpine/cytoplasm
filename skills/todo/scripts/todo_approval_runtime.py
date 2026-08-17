"""Runtime dependency assembly for the todo approval adapter."""
from __future__ import annotations

import importlib
import os
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
