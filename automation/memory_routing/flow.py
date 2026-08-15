from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypeAlias

from .adapters import AdapterOutcome, AdapterResult, MemoryWrite, dedupe_key
from .classifier import MemoryRoute, MemoryTarget, classify_memory_request

FlowOutcome: TypeAlias = Literal[
    "stored",
    "duplicate",
    "not_persisted",
    "sensitive_rejected",
    "store_rejected",
    "store_failure",
    "partial_rejection",
    "partial_failure",
]
WritableTarget: TypeAlias = Literal["wiki", "memory_md", "skill", "tasks"]
_EMPTY_SENSITIVITY: Final[frozenset[str]] = frozenset[str]()


class MemoryClassifier(Protocol):
    def __call__(
        self,
        text: str,
        *,
        sensitivity: frozenset[str] = _EMPTY_SENSITIVITY,
    ) -> MemoryRoute: ...


class StoreAdapter(Protocol):
    def __call__(self, write: MemoryWrite) -> AdapterResult: ...


@dataclass(frozen=True, slots=True)
class MemoryRequest:
    title: str
    body: str
    tags: tuple[str, ...] = ()
    sensitivity: frozenset[str] = _EMPTY_SENSITIVITY
    approved_sensitive: bool = False


@dataclass(frozen=True, slots=True)
class MemoryFlowAdapters:
    wiki: StoreAdapter
    memory_md: StoreAdapter
    skill: StoreAdapter
    tasks: StoreAdapter


@dataclass(frozen=True, slots=True)
class CompletedStoreResult:
    target: MemoryTarget
    outcome: AdapterOutcome
    detail: str


@dataclass(frozen=True, slots=True)
class NotAttemptedStoreResult:
    target: MemoryTarget
    outcome: Literal["not_attempted"]
    detail: str


StoreResult: TypeAlias = CompletedStoreResult | NotAttemptedStoreResult


@dataclass(frozen=True, slots=True)
class MemoryFlowResult:
    route: MemoryRoute
    outcome: FlowOutcome
    idempotency_key: str
    canonical: StoreResult | None
    co_writes: tuple[StoreResult, ...]


def classify_then_store(
    request: MemoryRequest,
    adapters: MemoryFlowAdapters,
    *,
    classifier: MemoryClassifier = classify_memory_request,
) -> MemoryFlowResult:
    """Classify exactly once and execute that verdict in canonical-first order."""
    route = classifier(request.body, sensitivity=request.sensitivity)
    key = dedupe_key(request.body)
    write = MemoryWrite(
        route=route,
        title=request.title,
        body=request.body,
        tags=request.tags,
        approved_sensitive=request.approved_sensitive,
    )

    canonical_target = _writable_target(route.canonical)
    if canonical_target is None:
        return MemoryFlowResult(route, "not_persisted", key, None, ())

    if route.needs_sensitive_approval and not request.approved_sensitive:
        detail = "sensitive content needs owner approval"
        return MemoryFlowResult(
            route,
            "sensitive_rejected",
            key,
            NotAttemptedStoreResult(canonical_target, "not_attempted", detail),
            _not_attempted(route.co_write, detail),
        )

    canonical = _store(canonical_target, write, adapters)
    match canonical.outcome:
        case "success" | "duplicate":
            co_writes = tuple(_store(target, write, adapters) for target in route.co_write)
            return MemoryFlowResult(
                route,
                _completed_outcome(canonical, co_writes),
                key,
                canonical,
                co_writes,
            )
        case "rejected":
            return MemoryFlowResult(
                route,
                "store_rejected",
                key,
                canonical,
                _not_attempted(route.co_write, "canonical store was rejected"),
            )
        case "retryable_failure":
            return MemoryFlowResult(
                route,
                "store_failure",
                key,
                canonical,
                _not_attempted(route.co_write, "canonical store failed"),
            )


def _writable_target(target: MemoryTarget) -> WritableTarget | None:
    match target:
        case "wiki" | "memory_md" | "skill" | "tasks":
            return target
        case "none":
            return None


def _store(
    target: MemoryTarget,
    write: MemoryWrite,
    adapters: MemoryFlowAdapters,
) -> CompletedStoreResult:
    match target:
        case "wiki":
            result = adapters.wiki(write)
            return CompletedStoreResult(target, result.outcome, result.detail)
        case "memory_md":
            result = adapters.memory_md(write)
            return CompletedStoreResult(target, result.outcome, result.detail)
        case "skill":
            result = adapters.skill(write)
            return CompletedStoreResult(target, result.outcome, result.detail)
        case "tasks":
            result = adapters.tasks(write)
            return CompletedStoreResult(target, result.outcome, result.detail)
        case "none":
            return CompletedStoreResult("none", "rejected", "none is not a writable target")


def _not_attempted(
    targets: tuple[MemoryTarget, ...],
    detail: str,
) -> tuple[NotAttemptedStoreResult, ...]:
    return tuple(NotAttemptedStoreResult(target, "not_attempted", detail) for target in targets)


def _completed_outcome(
    canonical: CompletedStoreResult,
    co_writes: tuple[CompletedStoreResult, ...],
) -> FlowOutcome:
    stored_new = False
    rejected = False
    failed = False
    for item in (canonical, *co_writes):
        match item.outcome:
            case "success":
                stored_new = True
                continue
            case "duplicate":
                continue
            case "rejected":
                rejected = True
                continue
            case "retryable_failure":
                failed = True
    if failed:
        return "partial_failure"
    if rejected:
        return "partial_rejection"
    return "stored" if stored_new else "duplicate"


__all__ = [
    "CompletedStoreResult",
    "FlowOutcome",
    "MemoryClassifier",
    "MemoryFlowAdapters",
    "MemoryFlowResult",
    "MemoryRequest",
    "NotAttemptedStoreResult",
    "StoreAdapter",
    "StoreResult",
    "WritableTarget",
    "classify_then_store",
]
