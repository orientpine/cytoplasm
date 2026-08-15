from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from automation.entity_preflight.audit import (
    PrivateJsonlAuditStore,
    input_sha256,
    operational_event,
)
from automation.entity_preflight.clarify import (
    ENTITY_CLARIFY_EXIT_CODE,
    ENTITY_CLARIFY_MARKER,
    render_clarify,
)
from automation.entity_preflight.contracts import (
    AuditMetadata,
    DecisionKind,
    DecisionReason,
    DetectedEntity,
    EntityKind,
    PreflightInput,
    RelationshipQuery,
    ResolutionCandidate,
    SourceKind,
    VerificationOutcome,
    VerificationRecord,
    WritePhase,
)
from automation.entity_preflight.policy import POLICY_SEED_PATH, decide, load_policy
from automation.entity_preflight.state import initial_state, transition

_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE = _ROOT / "automation" / "entity_preflight"
_POLICY = load_policy(_ROOT / "configs" / "entity-preflight.json")


def _request(raw: str, *, with_entity: bool = True) -> PreflightInput:
    if not with_entity:
        return PreflightInput("req-0", raw, "google_tasks", "create", ())
    surface = "김가상"
    start = raw.index(surface)
    entity = DetectedEntity("m-1", surface, EntityKind.PERSON, start, start + len(surface))
    query = RelationshipQuery(
        "q-1",
        entity.mention_id,
        "attends_organization",
        EntityKind.ORGANIZATION,
        "김가상이 다니는 기관은?",
    )
    return PreflightInput("req-1", raw, "google_calendar", "create", (entity,), (query,))


def _audit(raw: str) -> AuditMetadata:
    return AuditMetadata(
        correlation_id="corr-fixture-1",
        policy_version=_POLICY.version,
        requested_at="2026-07-27T00:00:00Z",
        actor="owner",
        purpose="external_write_preflight",
        input_sha256=input_sha256(raw),
        sensitive_audit_ref="private://fixture/audit-1",
    )


def _candidate(
    candidate_id: str,
    normalized: str,
    confidence: float,
    source: SourceKind,
) -> ResolutionCandidate:
    return ResolutionCandidate(
        candidate_id=candidate_id,
        mention_id="m-1",
        source=source,
        normalized_value=normalized,
        display_value=normalized,
        confidence=confidence,
        source_ref=f"private://fixture/{candidate_id}",
        relationship_query_id="q-1",
    )


def test_single_high_confidence_candidate_is_auto_selected() -> None:
    raw = "김가상 기관의 공개행사를 일정에 추가해줘"
    request = _request(raw)
    result = decide(
        request,
        (_candidate("c-1", "샘플어린이집", 0.98, SourceKind.PERSONAL_RAG),),
        _audit(raw),
        _POLICY,
    )

    assert result.decision == DecisionKind.AUTO_SELECTED
    assert result.reason == DecisionReason.SINGLE_HIGH_CONFIDENCE
    assert result.needs_confirmation is False
    assert result.selected[0].normalized_value == "샘플어린이집"


def test_conflicting_candidates_require_confirmation() -> None:
    raw = "김가상 기관의 공개행사를 일정에 추가해줘"
    request = _request(raw)
    result = decide(
        request,
        (
            _candidate("c-1", "샘플어린이집", 0.96, SourceKind.ADDRESSBOOK_CONTACTS),
            _candidate("c-2", "예시유치원", 0.92, SourceKind.ADDRESSBOOK_ORGANIZATION),
        ),
        _audit(raw),
        _POLICY,
    )

    assert result.decision == DecisionKind.CONFIRMATION_REQUIRED
    assert result.reason == DecisionReason.CANDIDATE_CONFLICT
    assert result.needs_confirmation is True
    assert result.selected == ()


def test_no_detected_proper_noun_passes_without_confirmation() -> None:
    raw = "오늘 저녁에 보고서 검토하기를 할 일에 추가해줘"
    result = decide(_request(raw, with_entity=False), (), _audit(raw), _POLICY)

    assert result.decision == DecisionKind.NOT_DETECTED
    assert result.reason == DecisionReason.NO_ENTITY
    assert result.needs_confirmation is False
    assert initial_state(result).phase == WritePhase.READY_TO_WRITE


def test_operational_event_contains_no_personal_raw_values() -> None:
    raw = "김가상 기관의 공개행사를 일정에 추가해줘"
    result = decide(
        _request(raw),
        (_candidate("c-1", "샘플어린이집", 0.98, SourceKind.HERMES_MEMORY),),
        _audit(raw),
        _POLICY,
    )

    rendered = json.dumps(operational_event(result), ensure_ascii=False)
    for forbidden in ("김가상", "샘플어린이집", "다니는 기관", "private://"):
        assert forbidden not in rendered
    assert "entity_preflight_decision" in rendered


def test_private_audit_store_enforces_permissions(tmp_path: Path) -> None:
    root = tmp_path / "audit"
    store = PrivateJsonlAuditStore(root)
    path = Path(store.append({"raw_text": "합성 개인정보 fixture"}))

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_successful_write_requires_api_requery_before_verified() -> None:
    raw = "오늘 저녁에 보고서 검토하기를 할 일에 추가해줘"
    decision = decide(_request(raw, with_entity=False), (), _audit(raw), _POLICY)
    state = initial_state(decision)
    state = transition(state, WritePhase.WRITE_IN_PROGRESS)
    state = transition(state, WritePhase.WRITE_SUCCEEDED, resource_id="fixture-resource-1")
    state = transition(state, WritePhase.VERIFYING)
    with pytest.raises(ValueError, match="requires an API requery"):
        transition(state, WritePhase.VERIFIED)

    verification = VerificationRecord(
        external_system="google_tasks",
        resource_id="fixture-resource-1",
        api_operation="tasks.tasks.get",
        queried_at="2026-07-27T00:00:01Z",
        outcome=VerificationOutcome.MATCH,
        expected_fingerprint="sha256:fixture-expected",
        observed_fingerprint="sha256:fixture-expected",
        sensitive_evidence_ref="private://fixture/requery-1",
    )
    state = transition(state, WritePhase.VERIFIED, verification=verification)
    assert state.phase == WritePhase.VERIFIED
    assert state.verification == verification


def _conflicting_decision() -> tuple[str, object]:
    raw = "김가상 기관의 공개행사를 일정에 추가해줘"
    return raw, decide(
        _request(raw),
        (
            _candidate("c-1", "샘플어린이집", 0.96, SourceKind.ADDRESSBOOK_CONTACTS),
            _candidate("c-2", "예시유치원", 0.92, SourceKind.ADDRESSBOOK_ORGANIZATION),
        ),
        _audit(raw),
        _POLICY,
    )


def test_unresolved_entity_is_clarified_in_conversation_not_by_approval() -> None:
    _, decision = _conflicting_decision()

    assert decision.needs_confirmation is True
    rendered = render_clarify(decision)

    assert rendered.startswith(ENTITY_CLARIFY_MARKER)
    assert ENTITY_CLARIFY_EXIT_CODE != 0
    assert DecisionReason.CANDIDATE_CONFLICT.value in rendered
    assert rendered.index("샘플어린이집") < rendered.index("예시유치원")
    for candidate_hint in ("addressbook.contacts", "0.96", "addressbook.organization", "0.92"):
        assert candidate_hint in rendered
    assert "private://" not in rendered


def test_clarify_text_is_deterministic() -> None:
    _, first = _conflicting_decision()
    _, second = _conflicting_decision()

    assert render_clarify(first) == render_clarify(second)


def test_clarify_refuses_a_decision_that_needs_no_confirmation() -> None:
    raw = "김가상 기관의 공개행사를 일정에 추가해줘"
    decision = decide(
        _request(raw),
        (_candidate("c-1", "샘플어린이집", 0.98, SourceKind.PERSONAL_RAG),),
        _audit(raw),
        _POLICY,
    )

    assert decision.needs_confirmation is False
    with pytest.raises(ValueError, match="no clarification"):
        render_clarify(decision)


def test_clarify_without_candidates_asks_for_the_value_directly() -> None:
    raw = "김가상 기관의 공개행사를 일정에 추가해줘"
    decision = decide(_request(raw), (), _audit(raw), _POLICY)

    assert decision.decision == DecisionKind.CONFIRMATION_REQUIRED
    assert decision.reason == DecisionReason.NO_CANDIDATE
    rendered = render_clarify(decision)
    assert DecisionReason.NO_CANDIDATE.value in rendered
    assert "김가상" in rendered


def test_unresolved_entity_waits_in_the_clarify_phase() -> None:
    _, decision = _conflicting_decision()
    state = initial_state(decision)

    assert state.phase == WritePhase.AWAITING_CLARIFY
    assert transition(state, WritePhase.CANCELLED, failure_code="owner_cancelled").phase == (
        WritePhase.CANCELLED
    )


def test_operational_event_reports_clarify_need_without_personal_values() -> None:
    _, decision = _conflicting_decision()
    event = operational_event(decision)

    assert event["needs_confirmation"] is True
    assert "김가상" not in json.dumps(event, ensure_ascii=False)


def test_thresholds_have_one_tracked_configuration_location() -> None:
    assert POLICY_SEED_PATH == _ROOT / "configs" / "entity-preflight.json"
    assert load_policy().version == _POLICY.version


def test_preflight_package_opens_no_owner_approval_surface() -> None:
    forbidden = (
        "ApprovalKind",
        "approval_lifecycle",
        "request_owner_approval",
        "resolve_reaction",
        "message_id",
    )
    offenders = [
        f"{path.name}:{token}"
        for path in sorted(_PACKAGE.glob("*.py"))
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
