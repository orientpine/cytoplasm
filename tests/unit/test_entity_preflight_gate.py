"""Behavioral contract for the common pre-write personal-entity guard."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

import pytest

from automation.entity_preflight.contracts import (
    DetectedEntity,
    EntityKind,
    JsonValue,
    PreflightInput,
    RelationshipQuery,
    ResolutionCandidate,
    SourceKind,
    VerificationOutcome,
    VerificationRecord,
    WriteReceipt,
)
from automation.entity_preflight.gate import (
    EntityClarificationRequired,
    EntityPreflightUnavailable,
    GateDependencies,
    GuardRequest,
    InMemoryIdempotencyStore,
    PostWriteVerificationFailed,
    guarded_write,
)

_RAW: Final = "송아한테 전화하라고 할 일 추가"
_NORMALIZED: Final = "송화"
_POLICY_VERSION: Final = "entity-preflight-v1"


class FakeSource:
    def __init__(
        self,
        source: SourceKind,
        candidates: tuple[ResolutionCandidate, ...],
        *,
        fails: bool = False,
    ) -> None:
        self.source = source
        self.candidates = candidates
        self.calls = 0
        self.fails = fails

    def resolve(
        self,
        request: PreflightInput,
        query: RelationshipQuery | None,
    ) -> tuple[ResolutionCandidate, ...]:
        del request, query
        self.calls += 1
        if self.fails:
            raise TimeoutError("synthetic resolver timeout")
        return self.candidates


class FakeWriter:
    def __init__(self, verification_outcome: VerificationOutcome = VerificationOutcome.MATCH) -> None:
        self.verification_outcome = verification_outcome
        self.writes: list[dict[str, str]] = []
        self.rereads = 0

    def write(self, payload: Mapping[str, JsonValue]) -> WriteReceipt:
        title = payload["title"]
        assert isinstance(title, str)
        self.writes.append({"title": title})
        return WriteReceipt("google_tasks", "task-fixture-1", "tasks.tasks.insert", "2026-07-29T00:00:00Z")

    def requery(self, receipt: WriteReceipt, expected_fingerprint: str) -> VerificationRecord:
        self.rereads += 1
        observed = expected_fingerprint if self.verification_outcome == VerificationOutcome.MATCH else "sha256:mismatch"
        return VerificationRecord(
            external_system=receipt.external_system,
            resource_id=receipt.resource_id,
            api_operation="tasks.tasks.get",
            queried_at="2026-07-29T00:00:01Z",
            outcome=self.verification_outcome,
            expected_fingerprint=expected_fingerprint,
            observed_fingerprint=observed,
            sensitive_evidence_ref="private://fixture/reread",
        )


class AuditSink:
    def __init__(self) -> None:
        self.events: list[Mapping[str, JsonValue]] = []

    def append(self, event: Mapping[str, JsonValue]) -> str:
        self.events.append(event)
        return "private://fixture/audit"


class OperationalLog:
    def __init__(self) -> None:
        self.events: list[Mapping[str, JsonValue]] = []

    def emit(self, event: Mapping[str, JsonValue]) -> None:
        self.events.append(event)


def _request(candidates: tuple[ResolutionCandidate, ...], *, no_entity: bool = False) -> GuardRequest:
    entities: tuple[DetectedEntity, ...] = ()
    queries: tuple[RelationshipQuery, ...] = ()
    if not no_entity:
        entity = DetectedEntity("m-1", "송아", EntityKind.PERSON, 0, 2)
        entities = (entity,)
        queries = (RelationshipQuery("q-1", entity.mention_id, "family_member", EntityKind.PERSON, "송아 가족"),)
    preflight = PreflightInput(
        request_id="task-request-fixture-1",
        raw_text=_RAW,
        target_system="google_tasks",
        operation="create",
        entities=entities,
        relationship_queries=queries,
    )
    source = FakeSource(SourceKind.PERSONAL_RAG, candidates)
    return GuardRequest(
        request=preflight,
        payload={"title": _RAW},
        sources=(source,),
        idempotency_key="task-request-fixture-1",
        actor="owner-fixture",
        purpose="task_create",
        requested_at="2026-07-29T00:00:00Z",
    )


def _candidate(candidate_id: str, value: str, confidence: float) -> ResolutionCandidate:
    return ResolutionCandidate(
        candidate_id=candidate_id,
        mention_id="m-1",
        source=SourceKind.PERSONAL_RAG,
        normalized_value=value,
        display_value=value,
        confidence=confidence,
        source_ref=f"private://fixture/{candidate_id}",
        relationship_query_id="q-1",
    )


def _dependencies(audit: AuditSink, log: OperationalLog) -> GateDependencies:
    return GateDependencies(audit_store=audit, operational_logger=log, idempotency_store=InMemoryIdempotencyStore())


def test_guard_normalizes_once_writes_once_and_records_private_audit() -> None:
    # Given: one high-confidence correction for the transcribed personal name.
    request = _request((_candidate("rag-songhwa", _NORMALIZED, 0.98),))
    writer = FakeWriter()
    audit = AuditSink()
    log = OperationalLog()

    # When: the common guard owns the preflight, write, and API re-read.
    result = guarded_write(request, writer, _dependencies(audit, log))

    # Then: only the normalized value reaches the connector and verification succeeds.
    assert writer.writes == [{"title": "송화한테 전화하라고 할 일 추가"}]
    assert writer.rereads == 1
    assert result.verification.outcome == VerificationOutcome.MATCH
    assert result.state.resource_id == "task-fixture-1"
    private = json.dumps(audit.events, ensure_ascii=False)
    assert _RAW in private
    assert _NORMALIZED in private
    assert '"target_system": "google_tasks"' in private
    assert '"task_id": "task-request-fixture-1"' in private
    operational = json.dumps(log.events, ensure_ascii=False)
    assert "송아" not in operational
    assert _NORMALIZED not in operational
    assert _POLICY_VERSION in operational


def test_guard_clarifies_once_and_never_starts_external_write() -> None:
    # Given: two credible but conflicting canonical values.
    request = _request((_candidate("rag-a", "송화", 0.98), _candidate("rag-b", "송희", 0.97)))
    writer = FakeWriter()
    audit = AuditSink()
    log = OperationalLog()
    dependencies = _dependencies(audit, log)

    # When: the first attempt reaches the ambiguity boundary.
    with pytest.raises(EntityClarificationRequired) as first:
        guarded_write(request, writer, dependencies)

    # Then: it renders exactly one deterministic clarification and writes nothing.
    assert first.value.should_render is True
    assert str(first.value).startswith("ENTITY-CLARIFY")
    assert "송화" in str(first.value)
    assert "송희" in str(first.value)
    assert writer.writes == []

    # When: the identical request is retried.
    with pytest.raises(EntityClarificationRequired) as second:
        guarded_write(request, writer, dependencies)

    # Then: no second confirmation is rendered and the connector remains untouched.
    assert second.value.should_render is False
    assert writer.writes == []


def test_guard_fails_safe_when_the_resolver_times_out() -> None:
    # Given: a personal entity whose local resolver cannot complete.
    request = _request(())
    failing_source = FakeSource(SourceKind.PERSONAL_RAG, (), fails=True)
    request = GuardRequest(
        request=request.request,
        payload=request.payload,
        sources=(failing_source,),
        idempotency_key=request.idempotency_key,
        actor=request.actor,
        purpose=request.purpose,
        requested_at=request.requested_at,
    )
    writer = FakeWriter()

    # When / Then: timeout fails closed before the external write.
    with pytest.raises(EntityPreflightUnavailable):
        guarded_write(request, writer, _dependencies(AuditSink(), OperationalLog()))
    assert writer.writes == []


def test_guard_passthrough_skips_resolver_for_request_without_personal_entity() -> None:
    # Given: a request with no detected personal entity and a source that would fail if called.
    request = _request((), no_entity=True)
    failing_source = FakeSource(SourceKind.PERSONAL_RAG, (), fails=True)
    request = GuardRequest(
        request=request.request,
        payload=request.payload,
        sources=(failing_source,),
        idempotency_key="no-entity-request",
        actor=request.actor,
        purpose=request.purpose,
        requested_at=request.requested_at,
    )
    writer = FakeWriter()

    # When: the guard receives the non-personal write.
    result = guarded_write(request, writer, _dependencies(AuditSink(), OperationalLog()))

    # Then: it preserves the existing one-write/one-read behavior without resolver latency.
    assert failing_source.calls == 0
    assert writer.writes == [{"title": _RAW}]
    assert writer.rereads == 1
    assert result.decision.request.entities == ()


def test_guard_replay_reuses_verified_result_without_second_external_write() -> None:
    # Given: a completed request and one shared idempotency store.
    request = _request((_candidate("rag-songhwa", _NORMALIZED, 0.98),))
    writer = FakeWriter()
    audit = AuditSink()
    log = OperationalLog()
    dependencies = _dependencies(audit, log)

    # When: the same idempotency key is submitted twice.
    first = guarded_write(request, writer, dependencies)
    second = guarded_write(request, writer, dependencies)

    # Then: the cached verified receipt is replayed, never the external operation.
    assert first.replayed is False
    assert second.replayed is True
    assert second.receipt == first.receipt
    assert writer.writes == [{"title": "송화한테 전화하라고 할 일 추가"}]
    assert writer.rereads == 1


def test_guard_records_and_reports_post_write_readback_mismatch() -> None:
    # Given: a successful external write followed by an API readback mismatch.
    request = _request((_candidate("rag-songhwa", _NORMALIZED, 0.98),))
    writer = FakeWriter(verification_outcome=VerificationOutcome.MISMATCH)
    audit = AuditSink()

    # When / Then: the mismatch is explicit and persisted as a verification failure.
    with pytest.raises(PostWriteVerificationFailed):
        guarded_write(request, writer, _dependencies(audit, OperationalLog()))
    assert writer.writes == [{"title": "송화한테 전화하라고 할 일 추가"}]
    assert writer.rereads == 1
    assert "verification_failed" in json.dumps(audit.events, ensure_ascii=False)
