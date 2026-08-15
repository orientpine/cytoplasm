"""Structured audit records and operational quality metrics for the entity preflight.

The guard already fails closed on an ambiguous personal proper noun. These tests
lock the *observability* of that guard: every guarded attempt leaves one
PII-free quality record that can be aggregated into rates, latency and alert
conditions, while the personal name itself stays in the access-controlled audit
store only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
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
    guarded_write,
)
from automation.entity_preflight.gate_quality import (
    ConfidenceBucket,
    GateQualityRecord,
    QualityDecision,
    decode_quality_event,
)
from automation.entity_preflight.gate_metrics import (
    AlertKind,
    aggregate_quality,
    evaluate_alerts,
    load_metrics_thresholds,
)
from automation.entity_preflight.policy import POLICY_SEED_PATH, PolicyError

_SURFACE: Final = "홍합서"
_NORMALIZED: Final = "홍합성"
_RIVAL: Final = "홍합순"
_RAW: Final = "홍합서한테 전화하라고 할 일 추가"
_LATENCY_MS: Final = 12


class FakeClock:
    """Monotonic fake advancing a fixed step per call, so latency is exact."""

    def __init__(self, step_ms: int = _LATENCY_MS) -> None:
        self.step_ns = step_ms * 1_000_000
        self.calls = 0

    def __call__(self) -> int:
        value = self.calls * self.step_ns
        self.calls += 1
        return value


class FakeSource:
    def __init__(self, candidates: tuple[ResolutionCandidate, ...], *, fails: bool = False) -> None:
        self.source = SourceKind.PERSONAL_RAG
        self.candidates = candidates
        self.fails = fails

    def resolve(
        self,
        request: PreflightInput,
        query: RelationshipQuery | None,
    ) -> tuple[ResolutionCandidate, ...]:
        del request, query
        if self.fails:
            raise TimeoutError("synthetic resolver timeout")
        return self.candidates


class FakeWriter:
    def __init__(self, outcome: VerificationOutcome = VerificationOutcome.MATCH) -> None:
        self.outcome = outcome
        self.writes: list[str] = []

    def write(self, payload: Mapping[str, JsonValue]) -> WriteReceipt:
        title = payload["title"]
        assert isinstance(title, str)
        self.writes.append(title)
        return WriteReceipt("google_tasks", "task-fixture-1", "tasks.tasks.insert", "created")

    def requery(self, receipt: WriteReceipt, expected_fingerprint: str) -> VerificationRecord:
        observed = expected_fingerprint if self.outcome == VerificationOutcome.MATCH else "sha256:other"
        return VerificationRecord(
            external_system=receipt.external_system,
            resource_id=receipt.resource_id,
            api_operation="tasks.tasks.get",
            queried_at="2026-07-29T00:00:01Z",
            outcome=self.outcome,
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


def _request(
    candidates: tuple[ResolutionCandidate, ...],
    *,
    no_entity: bool = False,
    fails: bool = False,
) -> GuardRequest:
    entities: tuple[DetectedEntity, ...] = ()
    queries: tuple[RelationshipQuery, ...] = ()
    if not no_entity:
        entity = DetectedEntity("m-1", _SURFACE, EntityKind.PERSON, 0, len(_SURFACE))
        entities = (entity,)
        queries = (RelationshipQuery("q-1", "m-1", "family_member", EntityKind.PERSON, "가족 관계"),)
    return GuardRequest(
        request=PreflightInput("task-fixture-1", _RAW, "google_tasks", "create", entities, queries),
        payload={"title": _RAW},
        sources=(FakeSource(candidates, fails=fails),),
        idempotency_key="corr-fixture-1",
        actor="owner-fixture",
        purpose="task_create",
        requested_at="2026-07-29T00:00:00Z",
    )


def _dependencies(audit: AuditSink, log: OperationalLog) -> GateDependencies:
    return GateDependencies(
        audit_store=audit,
        operational_logger=log,
        idempotency_store=InMemoryIdempotencyStore(),
        monotonic_ns=FakeClock(),
    )


def _quality_records(log: OperationalLog) -> list[GateQualityRecord]:
    decoded = (decode_quality_event(event) for event in log.events)
    return [record for record in decoded if record is not None]


def _synthetic(
    decision: QualityDecision,
    *,
    latency_ms: int = _LATENCY_MS,
    blocked: bool = True,
    verification: VerificationOutcome | None = None,
) -> GateQualityRecord:
    return GateQualityRecord(
        decision_id="corr-synthetic",
        sensitive_audit_ref="private://entity-preflight/corr-synthetic",
        channel="google_tasks",
        surface="create",
        decision=decision,
        reason="single_high_confidence",
        confidence_bucket=ConfidenceBucket.VERY_HIGH,
        source_kinds=("personal_rag",),
        entity_count=1,
        latency_ms=latency_ms,
        external_write_blocked=blocked,
        verification=verification,
        policy_version="entity-preflight-v1",
    )


def _verified_batch(count: int) -> tuple[GateQualityRecord, ...]:
    return tuple(
        _synthetic(
            QualityDecision.AUTO_NORMALIZED,
            latency_ms=(index + 1) * 10,
            blocked=False,
            verification=VerificationOutcome.MATCH,
        )
        for index in range(count)
    )


def test_verified_write_emits_one_pii_free_quality_record() -> None:
    # Given: one high-confidence correction of a mis-transcribed personal name.
    audit, log = AuditSink(), OperationalLog()
    writer = FakeWriter()

    # When: the guard completes the write and its API readback.
    guarded_write(_request((_candidate("rag-1", _NORMALIZED, 0.98),)), writer, _dependencies(audit, log))

    # Then: exactly one quality record describes the attempt, with no personal value.
    records = _quality_records(log)
    assert len(records) == 1
    record = records[0]
    assert record.decision == QualityDecision.AUTO_NORMALIZED
    assert record.channel == "google_tasks"
    assert record.surface == "create"
    assert record.confidence_bucket == ConfidenceBucket.VERY_HIGH
    assert record.source_kinds == ("personal_rag",)
    assert record.entity_count == 1
    assert record.latency_ms == _LATENCY_MS
    assert record.external_write_blocked is False
    assert record.verification == VerificationOutcome.MATCH


def test_conflicting_candidates_record_a_blocked_confirmation_request() -> None:
    # Given: two credible canonical values for the same mention.
    audit, log = AuditSink(), OperationalLog()
    writer = FakeWriter()
    request = _request((_candidate("rag-a", _NORMALIZED, 0.98), _candidate("rag-b", _RIVAL, 0.97)))

    # When: the guard reaches the ambiguity boundary.
    with pytest.raises(EntityClarificationRequired):
        guarded_write(request, writer, _dependencies(audit, log))

    # Then: the record proves the external write was blocked before the connector.
    record = _quality_records(log)[0]
    assert record.decision == QualityDecision.CONFIRMATION_REQUESTED
    assert record.reason == "candidate_conflict"
    assert record.confidence_bucket == ConfidenceBucket.NONE
    assert record.external_write_blocked is True
    assert record.verification is None
    assert writer.writes == []


def test_absent_candidates_record_an_unresolved_decision() -> None:
    # Given: a detected personal mention that no source can answer.
    audit, log = AuditSink(), OperationalLog()

    # When: the guard runs out of candidates.
    with pytest.raises(EntityClarificationRequired):
        guarded_write(_request(()), FakeWriter(), _dependencies(audit, log))

    # Then: the outcome is counted as unresolved, not as a confirmation request.
    record = _quality_records(log)[0]
    assert record.decision == QualityDecision.UNRESOLVED
    assert record.reason == "no_candidate"
    assert record.external_write_blocked is True


def test_resolver_failure_records_an_error_with_the_write_blocked() -> None:
    # Given: a local resolver that cannot complete.
    audit, log = AuditSink(), OperationalLog()
    writer = FakeWriter()

    # When: the guard fails closed before the connector.
    with pytest.raises(EntityPreflightUnavailable):
        guarded_write(_request((), fails=True), writer, _dependencies(audit, log))

    # Then: the error is measurable and still names no personal value.
    record = _quality_records(log)[0]
    assert record.decision == QualityDecision.ERROR
    assert record.channel == "google_tasks"
    assert record.surface == "create"
    assert record.latency_ms == _LATENCY_MS
    assert record.external_write_blocked is True
    assert writer.writes == []


def test_request_without_a_personal_entity_records_no_quality_event() -> None:
    # Given: a write that carries no personal proper noun at all.
    audit, log = AuditSink(), OperationalLog()

    # When: the guard passes it through.
    guarded_write(_request((), no_entity=True), FakeWriter(), _dependencies(audit, log))

    # Then: no preflight quality is measured, so the rates stay undiluted.
    assert _quality_records(log) == []


def test_quality_record_traces_back_to_its_decision_rationale() -> None:
    # Given: a completed guarded write.
    audit, log = AuditSink(), OperationalLog()
    guarded_write(_request((_candidate("rag-1", _NORMALIZED, 0.98),)), FakeWriter(), _dependencies(audit, log))

    # When: an operator starts from the operational record's decision id.
    record = _quality_records(log)[0]
    rationale = [
        event
        for event in audit.events
        if event.get("event") == "entity_preflight_decision"
        and event.get("decision_id") == record.decision_id
    ]

    # Then: exactly one private record holds why the guard chose that value.
    assert len(rationale) == 1
    assert rationale[0]["decision_method"] == "single_high_confidence"
    assert rationale[0]["candidates"] != []
    assert record.sensitive_audit_ref.endswith(record.decision_id)


def test_synthetic_personal_name_never_reaches_logs_or_metric_labels() -> None:
    # Given: a synthetic personal name and its mis-transcribed surface.
    audit, log = AuditSink(), OperationalLog()
    guarded_write(_request((_candidate("rag-1", _NORMALIZED, 0.98),)), FakeWriter(), _dependencies(audit, log))

    # When: the operational stream is aggregated into metrics and alerts.
    metrics = aggregate_quality(_quality_records(log))
    alerts = evaluate_alerts(metrics, load_metrics_thresholds())
    operational = json.dumps(log.events, ensure_ascii=False)
    labels = json.dumps(
        [metrics.to_event(), [alert.to_event() for alert in alerts]], ensure_ascii=False
    )

    # Then: the name appears zero times outside the access-controlled store.
    for personal in (_SURFACE, _NORMALIZED, _RAW):
        assert operational.count(personal) == 0
        assert labels.count(personal) == 0
    assert json.dumps(audit.events, ensure_ascii=False).count(_NORMALIZED) > 0


def test_aggregate_quality_reports_rates_and_p95_latency() -> None:
    # Given: twenty attempts with a known decision mix and known latencies.
    records = (
        *_verified_batch(15),
        _synthetic(
            QualityDecision.AUTO_NORMALIZED,
            latency_ms=160,
            blocked=False,
            verification=VerificationOutcome.MISMATCH,
        ),
        _synthetic(QualityDecision.CONFIRMATION_REQUESTED, latency_ms=170),
        _synthetic(QualityDecision.CONFIRMATION_REQUESTED, latency_ms=180),
        _synthetic(QualityDecision.UNRESOLVED, latency_ms=190),
        _synthetic(QualityDecision.ERROR, latency_ms=200),
    )

    # When: the aggregation API summarizes them.
    metrics = aggregate_quality(records)

    # Then: each published quality figure matches the mix exactly.
    assert metrics.total == 20
    assert metrics.auto_normalization_rate == 0.8
    assert metrics.confirmation_request_rate == 0.1
    assert metrics.unresolved_rate == 0.05
    assert metrics.verification_failure_rate == 0.0625
    assert metrics.p95_latency_ms == 190
    assert metrics.bypass_count == 0


def test_aggregate_quality_of_an_empty_window_is_all_zero() -> None:
    # Given / When: no guarded attempt happened in the window.
    metrics = aggregate_quality(())

    # Then: rates are zero rather than undefined, so alerts stay quiet.
    assert metrics.total == 0
    assert metrics.auto_normalization_rate == 0.0
    assert metrics.verification_failure_rate == 0.0
    assert metrics.p95_latency_ms == 0


def test_alerts_fire_on_auto_normalization_spike_and_guard_bypass() -> None:
    # Given: the tracked thresholds and two abnormal windows.
    thresholds = load_metrics_thresholds()
    spike = aggregate_quality(_verified_batch(thresholds.min_sample_size))
    bypass = aggregate_quality((_synthetic(QualityDecision.CONFIRMATION_REQUESTED, blocked=False),))

    # When: each window is evaluated.
    spike_kinds = {alert.kind for alert in evaluate_alerts(spike, thresholds)}
    bypass_kinds = {alert.kind for alert in evaluate_alerts(bypass, thresholds)}

    # Then: an all-automatic window and an unblocked non-automatic decision both alert.
    assert AlertKind.AUTO_NORMALIZATION_SPIKE in spike_kinds
    assert AlertKind.GUARD_BYPASS in bypass_kinds


def test_alerts_stay_quiet_for_a_healthy_window() -> None:
    # Given: a window with a normal confirmation share and low latency.
    thresholds = load_metrics_thresholds()
    healthy = aggregate_quality(
        (
            *_verified_batch(thresholds.min_sample_size),
            *(_synthetic(QualityDecision.CONFIRMATION_REQUESTED) for _ in range(4)),
        )
    )

    # When / Then: nothing is reported.
    assert evaluate_alerts(healthy, thresholds) == ()


def test_metric_thresholds_live_in_the_single_policy_seed() -> None:
    # Given: the one tracked seed that already owns the decision thresholds.
    seed = json.loads(POLICY_SEED_PATH.read_text(encoding="utf-8"))

    # When: the metric thresholds are loaded with the default path.
    thresholds = load_metrics_thresholds()

    # Then: they come from that same file and nowhere else.
    assert thresholds == load_metrics_thresholds(POLICY_SEED_PATH)
    assert seed["metrics"]["max_auto_normalization_rate"] == thresholds.max_auto_normalization_rate
    assert seed["metrics"]["max_bypass_count"] == thresholds.max_bypass_count
    assert seed["metrics"]["min_sample_size"] == thresholds.min_sample_size


def test_metric_thresholds_fail_closed_when_the_seed_is_incomplete(tmp_path: Path) -> None:
    # Given: a seed without the metrics block.
    path = tmp_path / "entity-preflight.json"
    path.write_text(json.dumps({"version": "seed-fixture"}), encoding="utf-8")

    # When / Then: loading refuses rather than inventing a threshold.
    with pytest.raises(PolicyError):
        load_metrics_thresholds(path)


def test_quality_event_round_trips_through_the_durable_record() -> None:
    # Given: one record written to the durable JSONL shape.
    record = _synthetic(QualityDecision.AUTO_NORMALIZED, blocked=False, verification=VerificationOutcome.MATCH)

    # When: the stored event is decoded again.
    decoded = decode_quality_event(record.to_event())

    # Then: the record survives, and unrelated audit events decode to nothing.
    assert decoded == record
    assert decode_quality_event({"event": "entity_preflight_decision"}) is None
