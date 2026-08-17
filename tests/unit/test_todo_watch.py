from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from automation.interop.approval_lease import FileKeyLease
from automation.interop.approval_surface import ChannelFacts
from automation.interop.external_effect_gate import ApprovalContext

if TYPE_CHECKING:
    from todo_approval_model import TodoApprovalRecord
    from todo_approval_store import TodoApprovalStore


_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "todo" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_OWNER = "owner-fixture"
_CHANNEL = "1526487935975952385"
_MESSAGE = "1530000000000000001"
_NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


@dataclass(slots=True)
class FakeDirectory:
    described: list[str]

    def owner_dm(self) -> str:
        raise AssertionError("the watcher must use the stored channel binding")

    def skill_approvals(self) -> str:
        raise AssertionError("the watcher must not resolve another surface")

    def describe(self, channel_id: str) -> ChannelFacts:
        self.described.append(channel_id)
        return ChannelFacts(1, "", (_OWNER,))


@dataclass(slots=True)
class FakeTransport:
    content: str
    reactions: dict[str, tuple[tuple[str, bool], ...]]
    calls: list[tuple[str, ...]]

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        self.calls.append(("get", channel_id, message_id))
        return self.content

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]:
        self.calls.append(("reactions", channel_id, message_id, emoji))
        return self.reactions.get(emoji, ())


@dataclass(frozen=True, slots=True)
class Fixture:
    todo: ModuleType
    store_module: ModuleType
    store: TodoApprovalStore
    record: TodoApprovalRecord
    argv: tuple[str, ...]
    transport: FakeTransport
    directory: FakeDirectory
    log: Path
    lease: FileKeyLease


@pytest.fixture(autouse=True)
def _runtime_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(_REPO))


def _fixture(root: Path, *, title: str = "합성 워처 과제") -> Fixture:
    todo = import_module("todo_cli")
    store_module = import_module("todo_approval_store")
    request = todo.TaskRequest("@default", title)
    argv = todo.insert_argv(request)
    decision = todo.evaluate(argv, context=ApprovalContext(None, _OWNER, False))
    spec = store_module.TodoApprovalSpec(
        f"todo:{decision.action_hash}",
        decision.action_hash,
        decision.target_id,
        import_module("todo_approval").masked_argv_summary(argv),
        "todo",
        "owner-dm",
        _CHANNEL,
        7,
    )
    store = store_module.TodoApprovalStore(root / "state")
    record = store.bind_message(store.prepare(spec, _NOW), _MESSAGE)
    return Fixture(
        todo,
        store_module,
        store,
        record,
        argv,
        FakeTransport(f"approval {decision.action_hash}", {}, []),
        FakeDirectory([]),
        root / "approvals.jsonl",
        FileKeyLease(root / "state" / "approval-leases"),
    )


def _run(item: Fixture, now: datetime, **kwargs: object) -> None:
    watch = import_module("todo_confirm_reaction_watch")
    watch.run_once(
        store=item.store,
        owner_id=_OWNER,
        transport=item.transport,
        directory=item.directory,
        approval_log=item.log,
        lease=item.lease,
        now=now,
        **kwargs,
    )


def test_owner_approval_appends_once_passes_real_gate_and_archives_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _fixture(tmp_path)
    item.transport.reactions["✅"] = ((_OWNER, False),)
    surface = import_module("automation.interop.approval_surface")
    validated: list[str] = []
    real_validate = surface.validate_stored_binding

    def validate(binding, directory, owner_id):
        validated.append(binding.channel_id)
        return real_validate(binding, directory, owner_id)

    monkeypatch.setattr(surface, "validate_stored_binding", validate)
    _run(item, _NOW + timedelta(minutes=1))
    _run(item, _NOW + timedelta(minutes=2))

    lines = item.log.read_text(encoding="utf-8").splitlines()
    archived = item.store.archives(item.record.key)
    assert len(lines) == 1
    assert item.todo.evaluate(
        item.argv,
        context=ApprovalContext(item.log, _OWNER, False),
    ).allowed
    assert [(entry.state.value, entry.outcome) for entry in archived] == [("archived", "approved")]
    assert item.store.active(item.record.key) is None
    assert validated == [_CHANNEL]


def test_cancel_wins_when_both_owner_reactions_exist(tmp_path: Path) -> None:
    item = _fixture(tmp_path)
    item.transport.reactions.update({"✅": ((_OWNER, False),), "⛔": ((_OWNER, False),)})
    _run(item, _NOW + timedelta(minutes=1))
    archived = item.store.archives(item.record.key)
    assert [(entry.state.value, entry.outcome) for entry in archived] == [("archived", "cancelled")]
    assert not item.log.exists()


def test_non_owner_and_bot_reactions_remain_pending(tmp_path: Path) -> None:
    item = _fixture(tmp_path)
    item.transport.reactions["✅"] = (("someone-else", False), (_OWNER, True))
    _run(item, _NOW + timedelta(minutes=1))
    assert item.store.active(item.record.key) == item.record
    assert item.store.archives(item.record.key) == ()
    assert not item.log.exists()


def test_hash_mismatch_consumes_nothing(tmp_path: Path) -> None:
    item = _fixture(tmp_path)
    item.transport.content = "approval for another hash"
    item.transport.reactions["✅"] = ((_OWNER, False),)
    _run(item, _NOW + timedelta(minutes=1))
    assert item.store.active(item.record.key) == item.record
    assert not item.log.exists()


def test_reaction_before_ttl_is_consumed_but_after_ttl_is_expired(tmp_path: Path) -> None:
    before = _fixture(tmp_path / "before", title="만료 직전")
    before.transport.reactions["✅"] = ((_OWNER, False),)
    _run(before, _NOW + before.store_module.TODO_APPROVAL_TTL - timedelta(seconds=1))
    assert before.store.archives(before.record.key)[0].outcome == "approved"
    assert len(before.log.read_text(encoding="utf-8").splitlines()) == 1

    after = _fixture(tmp_path / "after", title="만료 직후")
    after.transport.reactions["✅"] = ((_OWNER, False),)
    _run(after, _NOW + after.store_module.TODO_APPROVAL_TTL + timedelta(seconds=1))
    archived = after.store.archives(after.record.key)
    assert [(entry.state.value, entry.outcome) for entry in archived] == [("expired", None)]
    assert not after.log.exists()


def test_approval_log_failure_leaves_generation_pending(tmp_path: Path) -> None:
    item = _fixture(tmp_path)
    item.transport.reactions["✅"] = ((_OWNER, False),)

    def fail(*_args: object, **_kwargs: object) -> bool:
        raise OSError("synthetic append failure")

    watch = import_module("todo_confirm_reaction_watch")
    with pytest.raises(watch.TodoWatchError):
        _run(item, _NOW + timedelta(minutes=1), record_approval=fail)
    assert item.store.active(item.record.key) == item.record
    assert item.store.archives(item.record.key) == ()


def test_archived_generations_are_not_polled(tmp_path: Path) -> None:
    item = _fixture(tmp_path)
    item.store.archive(item.record, item.store_module.ApprovalState.EXPIRED, None)
    _run(item, _NOW + timedelta(days=2))
    assert item.transport.calls == []
    assert not item.log.exists()


def test_archive_outcome_is_closed_to_owner_decisions(tmp_path: Path) -> None:
    item = _fixture(tmp_path)
    with pytest.raises(item.store_module.TodoApprovalStoreError):
        item.store.archive(item.record, item.store_module.ApprovalState.ARCHIVED, "unexpected")
    assert item.store.active(item.record.key) == item.record


def test_deploy_installs_unique_no_agent_watcher_behind_provenance_guard() -> None:
    text = (_REPO / "skills" / "todo" / "deploy.sh").read_text(encoding="utf-8")
    assert "deploy_provenance_check" in text
    assert "push_file" in text
    assert ".hermes/scripts/todo_confirm_reaction_watch.py" in text
    assert "hermes cron create" in text
    assert "--no-agent" in text
