from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final, final

from automation.memory_routing.adapters import AdapterResult, MemoryWrite, dedupe_key
from automation.memory_routing.classifier import MemoryRoute
from automation.memory_routing.flow import (
    MemoryFlowAdapters,
    MemoryRequest,
    classify_then_store,
)

_SUCCESS: Final = AdapterResult("success", "stored")
_RETRYABLE: Final = AdapterResult("retryable_failure", "store unavailable")
_EMPTY_SENSITIVITY: Final[frozenset[str]] = frozenset[str]()

_WIKI_AND_MEMORY: Final = MemoryRoute(
    canonical="wiki",
    co_write=("memory_md",),
    never_persist=False,
    needs_sensitive_approval=False,
    reason="stable-global-preference",
)
_SKILL_ONLY: Final = MemoryRoute(
    canonical="skill",
    co_write=(),
    never_persist=False,
    needs_sensitive_approval=False,
    reason="reusable-procedure",
)
_TASKS_ONLY: Final = MemoryRoute(
    canonical="tasks",
    co_write=(),
    never_persist=True,
    needs_sensitive_approval=False,
    reason="temporary-status",
)
_SESSION_ONLY: Final = MemoryRoute(
    canonical="none",
    co_write=(),
    never_persist=True,
    needs_sensitive_approval=False,
    reason="uncertain-conservative",
)
_SENSITIVE: Final = MemoryRoute(
    canonical="wiki",
    co_write=("memory_md",),
    never_persist=False,
    needs_sensitive_approval=True,
    reason="sensitive-needs-approval",
)


@dataclass(frozen=True, slots=True)
class ClassifierCall:
    text: str
    sensitivity: frozenset[str]


@final
class RecordingClassifier:
    """Mutable test fake whose purpose is counting boundary calls."""

    __slots__: ClassVar[tuple[str, ...]] = ("calls", "route")
    route: MemoryRoute
    calls: list[ClassifierCall]

    def __init__(self, route: MemoryRoute) -> None:
        self.route = route
        self.calls = []

    def __call__(
        self,
        text: str,
        *,
        sensitivity: frozenset[str] = _EMPTY_SENSITIVITY,
    ) -> MemoryRoute:
        self.calls.append(ClassifierCall(text, sensitivity))
        return self.route


@final
class RecordingStore:
    """Idempotent adapter fake with deterministic per-attempt outcomes."""

    __slots__: ClassVar[tuple[str, ...]] = ("calls", "created_keys", "events", "name", "outcomes")
    name: str
    events: list[str]
    outcomes: tuple[AdapterResult, ...]
    calls: list[MemoryWrite]
    created_keys: set[str]

    def __init__(
        self,
        name: str,
        events: list[str],
        outcomes: tuple[AdapterResult, ...] = (),
    ) -> None:
        self.name = name
        self.events = events
        self.outcomes = outcomes
        self.calls = []
        self.created_keys = set()

    def __call__(self, write: MemoryWrite) -> AdapterResult:
        self.events.append(self.name)
        self.calls.append(write)
        key = dedupe_key(write.body)
        if key in self.created_keys:
            return AdapterResult("duplicate", "already stored")
        index = len(self.calls) - 1
        result = self.outcomes[index] if index < len(self.outcomes) else _SUCCESS
        match result.outcome:
            case "success":
                self.created_keys.add(key)
                return result
            case "duplicate" | "rejected" | "retryable_failure":
                return result


@dataclass(frozen=True, slots=True)
class StorePlan:
    wiki: tuple[AdapterResult, ...] = ()
    memory_md: tuple[AdapterResult, ...] = ()
    skill: tuple[AdapterResult, ...] = ()
    tasks: tuple[AdapterResult, ...] = ()


_DEFAULT_STORE_PLAN: Final = StorePlan()


@final
class StoreHarness:
    """Own the four recording adapters used by one isolated flow test."""

    __slots__: ClassVar[tuple[str, ...]] = ("events", "memory_md", "skill", "tasks", "wiki")
    events: list[str]
    wiki: RecordingStore
    memory_md: RecordingStore
    skill: RecordingStore
    tasks: RecordingStore

    def __init__(self, plan: StorePlan = _DEFAULT_STORE_PLAN) -> None:
        self.events = []
        self.wiki = RecordingStore("wiki", self.events, plan.wiki)
        self.memory_md = RecordingStore("memory_md", self.events, plan.memory_md)
        self.skill = RecordingStore("skill", self.events, plan.skill)
        self.tasks = RecordingStore("tasks", self.events, plan.tasks)

    def adapters(self) -> MemoryFlowAdapters:
        return MemoryFlowAdapters(
            wiki=self.wiki,
            memory_md=self.memory_md,
            skill=self.skill,
            tasks=self.tasks,
        )


def test_classifies_once_then_writes_wiki_before_allowed_memory_md() -> None:
    # Given: one stable global memory request and injected stores.
    request = MemoryRequest(title="언어 선호", body="항상 짧은 한국어 답변을 선호해. 기억해줘")
    classifier = RecordingClassifier(_WIKI_AND_MEMORY)
    stores = StoreHarness()

    # When: the single flow entry point handles the request.
    result = classify_then_store(request, stores.adapters(), classifier=classifier)

    # Then: one verdict drives the canonical store first and its allowed co-write.
    assert classifier.calls == [ClassifierCall(request.body, frozenset())]
    assert stores.events == ["wiki", "memory_md"]
    assert stores.wiki.calls[0].route is _WIKI_AND_MEMORY
    assert stores.memory_md.calls[0].route is _WIKI_AND_MEMORY
    assert result.outcome == "stored"
    assert result.idempotency_key == dedupe_key(request.body)


def test_routes_reusable_procedure_only_to_skill() -> None:
    # Given: classification selected the skill target.
    stores = StoreHarness()
    classifier = RecordingClassifier(_SKILL_ONLY)

    # When: the flow handles the procedure.
    result = classify_then_store(
        MemoryRequest(title="검토 절차", body="검토 체크리스트를 기억해줘"),
        stores.adapters(),
        classifier=classifier,
    )

    # Then: no unnecessary wiki or MEMORY.md write occurs.
    assert stores.events == ["skill"]
    assert result.canonical is not None
    assert result.canonical.target == "skill"


def test_routes_temporary_status_only_to_tasks() -> None:
    # Given: classification selected expiring task context.
    stores = StoreHarness()
    classifier = RecordingClassifier(_TASKS_ONLY)

    # When: the flow handles the temporary state.
    result = classify_then_store(
        MemoryRequest(title="출장", body="이번 주까지 출장 중이라고 기억해줘"),
        stores.adapters(),
        classifier=classifier,
    )

    # Then: tasks is the sole write despite never_persist being true.
    assert stores.events == ["tasks"]
    assert result.outcome == "stored"


def test_keeps_session_only_request_out_of_every_store() -> None:
    # Given: classification selected no persistence target.
    stores = StoreHarness()
    classifier = RecordingClassifier(_SESSION_ONLY)

    # When: the flow handles the session-only request.
    result = classify_then_store(
        MemoryRequest(title="질문", body="이번 결과를 설명해줘"),
        stores.adapters(),
        classifier=classifier,
    )

    # Then: nothing durable is produced.
    assert stores.events == []
    assert result.outcome == "not_persisted"
    assert result.canonical is None


def test_sensitive_request_is_explicitly_rejected_before_store_without_approval() -> None:
    # Given: the boundary marked the request sensitive and approval is absent.
    stores = StoreHarness()
    classifier = RecordingClassifier(_SENSITIVE)
    request = MemoryRequest(
        title="민감 사실",
        body="민감한 연구 사실을 기억해줘",
        sensitivity=frozenset({"sensitive"}),
    )

    # When: the flow handles the request.
    result = classify_then_store(request, stores.adapters(), classifier=classifier)

    # Then: rejection is explicit and no adapter can create an external effect.
    assert stores.events == []
    assert result.outcome == "sensitive_rejected"
    assert result.canonical is not None
    assert result.canonical.outcome == "not_attempted"
    assert [item.outcome for item in result.co_writes] == ["not_attempted"]


def test_store_failure_stops_before_secondary_and_is_not_sensitive_rejection() -> None:
    # Given: the canonical wiki adapter has a retryable infrastructure failure.
    stores = StoreHarness(StorePlan(wiki=(_RETRYABLE,)))
    classifier = RecordingClassifier(_WIKI_AND_MEMORY)

    # When: the flow handles the request.
    result = classify_then_store(
        MemoryRequest(title="언어", body="항상 한국어를 선호해. 기억해줘"),
        stores.adapters(),
        classifier=classifier,
    )

    # Then: failure is distinct, and secondary storage remains explicitly unattempted.
    assert stores.events == ["wiki"]
    assert result.outcome == "store_failure"
    assert result.canonical is not None
    assert result.canonical.outcome == "retryable_failure"
    assert [item.outcome for item in result.co_writes] == ["not_attempted"]


def test_partial_failure_preserves_canonical_success_and_secondary_failure() -> None:
    # Given: wiki succeeds but the allowed MEMORY.md co-write fails.
    stores = StoreHarness(StorePlan(memory_md=(_RETRYABLE,)))
    classifier = RecordingClassifier(_WIKI_AND_MEMORY)

    # When: the flow handles the request.
    result = classify_then_store(
        MemoryRequest(title="언어", body="항상 한국어를 선호해. 기억해줘"),
        stores.adapters(),
        classifier=classifier,
    )

    # Then: the caller can report exactly which store did and did not succeed.
    assert result.outcome == "partial_failure"
    assert result.canonical is not None
    assert result.canonical.outcome == "success"
    assert [item.outcome for item in result.co_writes] == ["retryable_failure"]


def test_identical_retry_uses_adapter_dedupe_then_retries_missing_secondary() -> None:
    # Given: the first canonical write succeeds while its co-write transiently fails.
    stores = StoreHarness(StorePlan(memory_md=(_RETRYABLE, _SUCCESS)))
    classifier = RecordingClassifier(_WIKI_AND_MEMORY)
    request = MemoryRequest(title="언어", body="항상 한국어를 선호해. 기억해줘")
    first = classify_then_store(request, stores.adapters(), classifier=classifier)

    # When: the caller retries the identical request.
    retried = classify_then_store(request, stores.adapters(), classifier=classifier)

    # Then: adapter content-key dedupe prevents a second canonical entry.
    assert first.outcome == "partial_failure"
    assert retried.outcome == "stored"
    assert retried.canonical is not None
    assert retried.canonical.outcome == "duplicate"
    assert [item.outcome for item in retried.co_writes] == ["success"]
    assert len(stores.wiki.created_keys) == 1
    assert len(stores.memory_md.created_keys) == 1
    assert len(classifier.calls) == 2
