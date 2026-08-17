from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class TodoError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ApprovalRequiredError(TodoError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 4)


class VerificationFailedError(TodoError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 6)


class TodoReconciliationRequiredError(TodoError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 7)


class EntityClarificationError(TodoError):
    def __init__(self, message: str, should_render: bool) -> None:
        super().__init__(message, 6)
        self.should_render = should_render


@dataclass(frozen=True, slots=True)
class TaskRequest:
    tasklist: str
    title: str
    notes: str | None = None
    due: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedTask:
    task_id: str
    title: str
    tasklist: str
    action_hash: str
    verified: bool


def _compact(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def insert_argv(request: TaskRequest) -> tuple[str, ...]:
    body: dict[str, Any] = {"title": request.title}
    if request.notes:
        body["notes"] = request.notes
    if request.due:
        body["due"] = request.due
    return (
        "gws", "tasks", "tasks", "insert",
        "--params", _compact({"tasklist": request.tasklist}),
        "--json", _compact(body),
    )


def get_argv(tasklist: str, task_id: str) -> tuple[str, ...]:
    return (
        "gws", "tasks", "tasks", "get",
        "--params", _compact({"task": task_id, "tasklist": tasklist}),
    )


def list_argv(tasklist: str) -> tuple[str, ...]:
    return (
        "gws", "tasks", "tasks", "list",
        "--params", _compact({"maxResults": 50, "tasklist": tasklist}),
    )
