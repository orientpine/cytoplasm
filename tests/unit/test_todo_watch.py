from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
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
_AGENT_CHAT_CHANNEL = "1526487935975952390"
_CHANNEL = "1526487935975952391"
_MESSAGE = "1530000000000000001"
_NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


@dataclass(slots=True)
class FakeDirectory:
    described: list[str]

    def owner_dm(self) -> str:
        raise AssertionError("the watcher must use the stored channel binding")

    def skill_approvals(self) -> str:
        raise AssertionError("the watcher must not resolve another surface")

    def agent_chat(self) -> str:
        return _AGENT_CHAT_CHANNEL

    def agent_chat_thread(self, _kind: object) -> str:
        raise AssertionError("the watcher must use the stored channel binding")

    def describe(self, channel_id: str) -> ChannelFacts:
        self.described.append(channel_id)
        return ChannelFacts(11, "승인-todo", (), _AGENT_CHAT_CHANNEL)


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
        "agent-chat-thread",
        _CHANNEL,
        7,
        tasklist=request.tasklist,
        title=request.title,
        notes=request.notes,
        due=request.due,
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


def test_approval_record_carries_the_execution_parameters(tmp_path: Path) -> None:
    """An approved generation must be replayable by the watcher.

    ``argv_summary`` is deliberately masked (``--json [masked]``), so it cannot rebuild
    the write. Without the four ``TaskRequest`` fields on the record, consuming the ✅
    leaves nobody able to say WHAT to create — which is exactly how repair ticket
    t_e3243dc5 recurred as occurrences 2 and 4.
    """
    store_module = import_module("todo_approval_store")
    spec = store_module.TodoApprovalSpec(
        "todo:hash-fixture",
        "hash-fixture",
        "tool:gws_tasks_mutation:fixture",
        "gws tasks tasks insert --params [masked] --json [masked]",
        "todo",
        "agent-chat-thread",
        _CHANNEL,
        7,
        tasklist="@default",
        title="합성 실행 파라미터 과제",
        notes="합성 메모",
        due="2026-08-26T00:00:00.000Z",
    )
    store = store_module.TodoApprovalStore(tmp_path / "state")
    record = store.bind_message(store.prepare(spec, _NOW), _MESSAGE)
    reloaded = store.active(record.key)

    assert reloaded is not None
    assert (reloaded.tasklist, reloaded.title, reloaded.notes, reloaded.due) == (
        "@default",
        "합성 실행 파라미터 과제",
        "합성 메모",
        "2026-08-26T00:00:00.000Z",
    )


@dataclass(slots=True)
class FakeGws:
    """In-memory ``gws`` stand-in — the watcher must never reach a real binary in tests."""

    stored_title: str = "합성 워처 과제"
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, argv: list[str]) -> dict[str, object]:
        self.calls.append(tuple(argv))
        if argv[3] == "insert":
            body = json.loads(argv[argv.index("--json") + 1])
            return {"id": "task-watch-1", "title": body["title"]}
        return {"id": "task-watch-1", "title": self.stored_title}

    @property
    def methods(self) -> list[str]:
        return [call[3] for call in self.calls]


def test_owner_approval_executes_the_approved_write_and_verifies_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """✅ alone must finish the job — insert then the mandatory tasks.get re-read.

    Before this, run_once appended the approval to the ledger and archived the
    generation, and stopped. The write only happened if a human re-ran
    ``todo_cli create``. Repair ticket t_e3243dc5 recorded that as occurrence 2
    (2026-08-24) and again as occurrence 4 (2026-08-25).
    """
    item = _fixture(tmp_path)
    item.transport.reactions["✅"] = ((_OWNER, False),)
    gws = FakeGws()
    monkeypatch.setattr(item.todo, "run_gws", gws)

    _run(item, _NOW + timedelta(minutes=1))

    archived = item.store.archives(item.record.key)[0]
    claims = import_module("todo_execution_claim").ApprovalClaimStore(item.store.root)
    assert gws.methods == ["insert", "get"]
    assert claims.status(archived) == "verified"


def test_a_later_tick_never_writes_the_same_approval_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconciler runs every minute; the receipt — not luck — stops the second write."""
    item = _fixture(tmp_path)
    item.transport.reactions["✅"] = ((_OWNER, False),)
    gws = FakeGws()
    monkeypatch.setattr(item.todo, "run_gws", gws)

    _run(item, _NOW + timedelta(minutes=1))
    _run(item, _NOW + timedelta(minutes=2))
    _run(item, _NOW + timedelta(minutes=3))

    assert gws.methods == ["insert", "get"]


def test_a_mismatched_reread_stays_unverified_and_is_not_rewritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write we cannot prove must never be reported as done, nor retried blindly.

    The re-read disagreeing with what was sent leaves the claim at ``write_started``:
    the row needs reconciliation, and a later tick must not insert a second task.
    """
    item = _fixture(tmp_path)
    item.transport.reactions["✅"] = ((_OWNER, False),)
    gws = FakeGws(stored_title="조용히 뒤바뀐 제목")
    monkeypatch.setattr(item.todo, "run_gws", gws)

    _run(item, _NOW + timedelta(minutes=1))

    archived = item.store.archives(item.record.key)[0]
    claims = import_module("todo_execution_claim").ApprovalClaimStore(item.store.root)
    assert claims.status(archived) == "write_started"

    _run(item, _NOW + timedelta(minutes=2))
    assert gws.methods == ["insert", "get"]


def test_a_generation_with_no_execution_parameters_is_reported_but_not_logged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A pre-fix approval can never become replayable — that is a state, not an event.

    Approvals archived before the execution parameters existed carry no title, so the
    reconciler cannot rebuild their argv and correctly skips them. But the reconciler runs
    every minute forever, and a property that can never change is not news: four such
    generations on the primary node would emit 5,760 identical lines a day and bury the
    outcomes that ARE events. The outcome still comes back to the caller.

    An empty title unambiguously means pre-fix: ``todo_cli request`` requires --title, so
    every generation written since carries one.
    """
    store_module = import_module("todo_approval_store")
    watch = import_module("todo_confirm_reaction_watch")
    store = store_module.TodoApprovalStore(tmp_path / "state")
    spec = store_module.TodoApprovalSpec(
        "todo:sha256:legacy",
        "sha256:legacy",
        "tool:gws_tasks_mutation:gws",
        "gws tasks tasks insert --params [masked] --json [masked]",
        "todo",
        "agent-chat-thread",
        _CHANNEL,
        7,
    )
    pending = store.bind_message(store.prepare(spec, _NOW), _MESSAGE)
    store.archive(pending, store_module.ApprovalState.ARCHIVED, "approved")

    results = watch.execute_approved_writes(
        store=store, approval_log=tmp_path / "approvals.jsonl", owner_id=_OWNER
    )

    assert results == (("todo:sha256:legacy", "legacy-unreplayable"),)
    assert capsys.readouterr().err == ""


def test_execution_failures_back_off_without_preventing_later_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed approved write is retried without printing the same failure every tick."""
    item = _fixture(tmp_path)
    item.transport.reactions["✅"] = ((_OWNER, False),)

    create_task = item.todo.create_task

    def fail_create_task(*_args: object, **_kwargs: object) -> None:
        raise OSError("expired credentials")

    monkeypatch.setattr(item.todo, "create_task", fail_create_task)
    for minute in range(1, 11):
        _run(item, _NOW + timedelta(minutes=minute))

    failures = [
        line for line in capsys.readouterr().err.splitlines() if line.startswith("TODO-EXEC failed:")
    ]
    assert 0 < len(failures) < 10

    gws = FakeGws()
    monkeypatch.setattr(item.todo, "create_task", create_task)
    monkeypatch.setattr(item.todo, "run_gws", gws)
    _run(item, _NOW + timedelta(minutes=20))

    archived = item.store.archives(item.record.key)[0]
    claims = import_module("todo_execution_claim").ApprovalClaimStore(item.store.root)
    assert gws.methods == ["insert", "get"]
    assert claims.status(archived) == "verified"
