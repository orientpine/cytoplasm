"""Owner-DM producer contracts for the todo request command."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import Outcome
from automation.interop.approval_surface import (
    ApprovalKind,
    ChannelFacts,
    resolve_new_binding,
)
from automation.interop.external_effect_gate import ApprovalContext

if TYPE_CHECKING:
    from automation.interop.approval_lifecycle import Verdict
    from todo_approval import ApprovalRuntime, TodoApprovalIntent
    from todo_approval_store import TodoApprovalStore


_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "todo" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_OWNER = "owner-fixture"
_CHANNEL = "1526487935975952385"
_NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


@dataclass(slots=True)
class FakeDirectory:
    described: list[str]

    def owner_dm(self) -> str:
        return _CHANNEL

    def skill_approvals(self) -> str:
        raise AssertionError("todo must not resolve the skill approval surface")

    def describe(self, channel_id: str) -> ChannelFacts:
        self.described.append(channel_id)
        return ChannelFacts(1, "", (_OWNER,))


@dataclass(slots=True)
class FakeTransport:
    messages: dict[str, str]
    calls: list[tuple[str, ...]]

    def post_message(self, channel_id: str, content: str) -> str:
        message_id = str(1530000000000000000 + len(self.messages) + 1)
        self.messages[message_id] = content
        self.calls.append(("post", channel_id, message_id))
        return message_id

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.calls.append(("react", channel_id, message_id, emoji))

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        self.calls.append(("get", channel_id, message_id))
        return self.messages.get(message_id)

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]:
        self.calls.append(("reactions", channel_id, message_id, emoji))
        return ()

    def delete_message(self, channel_id: str, message_id: str) -> None:
        self.calls.append(("delete", channel_id, message_id))


def _intent(todo: ModuleType, approval: ModuleType, title: str = "합성 승인 과제") -> TodoApprovalIntent:
    request = todo.TaskRequest("@default", title, notes="본문 비저장", due="2026-08-20T00:00:00Z")
    argv = todo.insert_argv(request)
    decision = todo.evaluate(argv, context=ApprovalContext(None, _OWNER, False))
    return approval.TodoApprovalIntent(
        action_hash=decision.action_hash,
        target_id=decision.target_id,
        argv_summary=approval.masked_argv_summary(argv),
        title=request.title,
        due=request.due,
    )


def _runtime(
    approval: ModuleType,
    store: TodoApprovalStore,
    transport: FakeTransport,
    directory: FakeDirectory,
    now: list[datetime],
    root: Path,
) -> ApprovalRuntime:
    binding = resolve_new_binding(ApprovalKind("todo"), directory, _OWNER)
    return approval.ApprovalRuntime(
        store=store,
        transport=transport,
        directory=directory,
        owner_id=_OWNER,
        binding=binding,
        lease=FileKeyLease(root / "leases"),
        journal=PostingJournal(root / "journal"),
        now=lambda: now[0],
    )


@pytest.fixture(autouse=True)
def _runtime_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(_REPO))


def test_request_posts_owner_dm_card_and_persists_binding(tmp_path: Path) -> None:
    # Given: an isolated store, directory, and fake Discord transport.
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    store_module = import_module("todo_approval_store")
    store = store_module.TodoApprovalStore(tmp_path / "state")
    transport = FakeTransport({}, [])
    directory = FakeDirectory([])
    intent = _intent(todo, approval)

    # When: the producer requests approval through the shared lifecycle façade.
    verdict = approval.request_approval(
        intent,
        _runtime(approval, store, transport, directory, [_NOW], tmp_path),
    )

    # Then: one DM card is posted, both reactions are primed, and sensitive notes are absent.
    assert verdict.outcome is Outcome.POSTED
    assert [call[0] for call in transport.calls] == ["post", "react", "react"]
    record = store.active(f"todo:{intent.action_hash}")
    assert record is not None
    assert (record.action_hash, record.target_id) == (intent.action_hash, intent.target_id)
    assert (record.kind, record.surface, record.channel_id) == ("todo", "owner-dm", _CHANNEL)
    assert "합성 승인 과제" in next(iter(transport.messages.values()))
    assert "본문 비저장" not in next(iter(transport.messages.values()))


def test_same_hash_reuses_live_pending_and_validates_stored_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one live pending request and a spy on stored-binding validation.
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    surface = import_module("automation.interop.approval_surface")
    store = import_module("todo_approval_store").TodoApprovalStore(tmp_path / "state")
    transport = FakeTransport({}, [])
    directory = FakeDirectory([])
    runtime = _runtime(approval, store, transport, directory, [_NOW], tmp_path)
    intent = _intent(todo, approval)
    first = approval.request_approval(intent, runtime)
    validations: list[str] = []
    real_validate = surface.validate_stored_binding

    def validate(binding, bound_directory, owner_id):
        validations.append(binding.channel_id)
        return real_validate(binding, bound_directory, owner_id)

    monkeypatch.setattr(surface, "validate_stored_binding", validate)

    # When: the exact same request is issued again before TTL.
    second = approval.request_approval(intent, runtime)

    # Then: lifecycle reuses the original message and posts nothing new.
    assert first.outcome is Outcome.POSTED
    assert second.outcome is Outcome.PENDING
    assert len(transport.messages) == 1
    assert validations == [_CHANNEL]


def test_expired_request_archives_then_posts_new_generation_without_delete(tmp_path: Path) -> None:
    # Given: generation one is live and the injected clock advances beyond 24 hours.
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    store_module = import_module("todo_approval_store")
    store = store_module.TodoApprovalStore(tmp_path / "state")
    transport = FakeTransport({}, [])
    directory = FakeDirectory([])
    now = [_NOW]
    runtime = _runtime(approval, store, transport, directory, now, tmp_path)
    intent = _intent(todo, approval)
    _ = approval.request_approval(intent, runtime)
    now[0] += store_module.TODO_APPROVAL_TTL + timedelta(seconds=1)

    # When: the same argv is requested after expiry.
    verdict = approval.request_approval(intent, runtime)

    # Then: a fresh generation is posted while the old Discord message is untouched.
    assert verdict.outcome is Outcome.POSTED
    assert len(transport.messages) == 2
    assert not any(call[0] == "delete" for call in transport.calls)
    assert store.active(f"todo:{intent.action_hash}").generation == 2
    assert [(item.generation, item.state.value) for item in store.archives(f"todo:{intent.action_hash}")] == [(1, "expired")]


def test_request_command_is_the_only_cli_entry_that_calls_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the adapter's runtime producer is replaced with an in-memory recorder.
    todo = import_module("todo_cli")
    approval = import_module("todo_approval")
    store = import_module("todo_approval_store").TodoApprovalStore(tmp_path / "todo-approvals")
    transport = FakeTransport({}, [])
    directory = FakeDirectory([])
    runtime = _runtime(approval, store, transport, directory, [_NOW], tmp_path)
    calls: list[str] = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_OWNER_ID", _OWNER)

    def request_cli(intent: TodoApprovalIntent, owner: str) -> Verdict:
        assert owner == _OWNER
        calls.append(intent.action_hash)
        return approval.request_approval(intent, runtime)

    monkeypatch.setattr(approval, "request_cli_approval", request_cli)

    # When: request and the read-only plan command are invoked through the public parser.
    assert todo.main(["request", "--title", "합성 요청"]) == 0
    assert todo.main(["plan", "--title", "합성 요청"]) == 0

    # Then: exactly request reached the approval producer.
    assert len(calls) == 1
    assert len(transport.messages) == 1
    assert store.active(f"todo:{calls[0]}") is not None


def test_todo_producer_is_registered_in_every_conformance_inventory() -> None:
    # Given / When: the shared conformance data is read as the mechanical producer registry.
    inventory = import_module("approval_conformance_inventory")
    producer = "skills/todo/scripts/todo_cli.py::_cmd_request"
    adapter = "skills/todo/scripts/todo_approval.py"

    # Then: no exemption substitutes for any lifecycle, kind, poster, or writer entry.
    assert inventory.APPROVAL_PRODUCERS[producer] == adapter
    assert inventory._LIFECYCLE_HOSTS[adapter] == adapter
    assert inventory.APPROVAL_KINDS[producer] == "todo"
    assert "skills/todo/scripts/todo_approval.py::TodoApprovalGate.post" in inventory._ADAPTER_POSTERS
    assert inventory._RECORD_WRITERS[
        "skills/todo/scripts/todo_approval.py::TodoApprovalGate.commit"
    ] == "skills/todo/scripts/todo_approval_store.py"
    assert producer not in inventory._EXEMPT
