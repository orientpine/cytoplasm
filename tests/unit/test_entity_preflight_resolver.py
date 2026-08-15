from __future__ import annotations

import socket
from dataclasses import dataclass, field

import pytest

from automation.entity_preflight.audit import input_sha256
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
)
from automation.entity_preflight.policy import load_policy
from automation.entity_preflight.resolver import (
    DataSourceFailure,
    detect_entities,
    resolve_preflight,
)


_POLICY = load_policy()


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    candidate_id: str
    source: SourceKind
    normalized_value: str
    confidence: float


@dataclass(slots=True)  # noqa: MUTABLE_OK
class FakeSource:
    """Injected source whose mutable call list proves resolver query behavior."""

    source: SourceKind
    candidates: tuple[ResolutionCandidate, ...]
    calls: list[RelationshipQuery | None] = field(default_factory=list)

    def resolve(
        self,
        request: PreflightInput,
        query: RelationshipQuery | None,
    ) -> tuple[ResolutionCandidate, ...]:
        self.calls.append(query)
        return self.candidates


@dataclass(slots=True)  # noqa: MUTABLE_OK
class RelationshipAwareSource:
    """Returns the fixture only when the rewritten query carries every signal."""

    source: SourceKind
    candidate: ResolutionCandidate
    calls: list[RelationshipQuery | None] = field(default_factory=list)

    def resolve(
        self,
        request: PreflightInput,
        query: RelationshipQuery | None,
    ) -> tuple[ResolutionCandidate, ...]:
        self.calls.append(query)
        if query is None:
            return ()
        required_signals = ("송아", "송화", "family_member", "send")
        if all(signal in query.question for signal in required_signals):
            return (self.candidate,)
        return ()


@dataclass(frozen=True, slots=True)
class FailingSource:
    source: SourceKind

    def resolve(
        self,
        request: PreflightInput,
        query: RelationshipQuery | None,
    ) -> tuple[ResolutionCandidate, ...]:
        raise DataSourceFailure(self.source, "synthetic_source_unavailable")


def _request(raw_text: str, surface: str) -> PreflightInput:
    start = raw_text.index(surface)
    entity = DetectedEntity("m-1", surface, EntityKind.PERSON, start, start + len(surface))
    query = RelationshipQuery(
        "q-1",
        entity.mention_id,
        "family_member",
        EntityKind.PERSON,
        f"{surface} 가족 구성원은 누구인가?",
    )
    return PreflightInput("req-fixture", raw_text, "mail", "send", (entity,), (query,))


def _audit(raw_text: str) -> AuditMetadata:
    return AuditMetadata(
        correlation_id="corr-resolver-fixture",
        policy_version=_POLICY.version,
        requested_at="2026-07-28T00:00:00Z",
        actor="owner",
        purpose="external_write_preflight",
        input_sha256=input_sha256(raw_text),
        sensitive_audit_ref="private://fixture/entity-preflight",
    )


def _candidate(spec: CandidateSpec) -> ResolutionCandidate:
    return ResolutionCandidate(
        candidate_id=spec.candidate_id,
        mention_id="m-1",
        source=spec.source,
        normalized_value=spec.normalized_value,
        display_value=spec.normalized_value,
        confidence=spec.confidence,
        source_ref=f"private://fixture/{spec.candidate_id}",
        relationship_query_id="q-1",
    )


@pytest.mark.parametrize(
    ("raw_text", "known_entities", "expected"),
    (
        ("할 일 제목: 모래 가족에게 연락", {"모래": EntityKind.PERSON}, (("모래", EntityKind.PERSON),)),
        ("메일 받는 사람: 라일락; 제목: 협업", {"라일락": EntityKind.PERSON}, (("라일락", EntityKind.PERSON),)),
        (
            "일정 참석자: 해솔; 설명: 파랑 프로젝트 검토",
            {"해솔": EntityKind.PERSON, "파랑": EntityKind.PROJECT},
            (("해솔", EntityKind.PERSON), ("파랑", EntityKind.PROJECT)),
        ),
    ),
)
def test_detect_entities_finds_known_personal_names_in_external_write_text(
    raw_text: str,
    known_entities: dict[str, EntityKind],
    expected: tuple[tuple[str, EntityKind], ...],
) -> None:
    # Given: a flattened Todo, mail, or calendar write payload and local entity hints.
    # When: the resolver detects proper-noun spans.
    detected = detect_entities(raw_text, known_entities)

    # Then: every caller-provided personal name retains its precise span and kind.
    assert tuple((item.surface, item.entity_kind) for item in detected) == expected
    assert all(raw_text[item.start : item.end] == item.surface for item in detected)


def test_normalizes_voice_transcription_when_relationship_query_finds_one_high_candidate() -> None:
    # Given: a voice transcription and a local RAG result available only through a rewritten query.
    raw_text = "송아에게 메일을 보내줘"
    candidate = _candidate(CandidateSpec("rag-songhwa", SourceKind.PERSONAL_RAG, "송화", 0.98))
    source = RelationshipAwareSource(SourceKind.PERSONAL_RAG, candidate)

    # When: personal-entity resolution runs before the external write.
    decision = resolve_preflight(_request(raw_text, "송아"), _audit(raw_text), (source,))

    # Then: the source, score, and private rationale stay attributed to the canonical target.
    assert decision.decision == DecisionKind.AUTO_SELECTED
    assert decision.needs_confirmation is False
    assert decision.selected[0].normalized_value == "송화"
    assert decision.candidates == (candidate,)
    assert source.calls[0] is not None


def test_requires_confirmation_when_same_surface_has_distinct_canonical_candidates() -> None:
    # Given: two separately attributable targets sharing the same input surface.
    raw_text = "다온에게 메일을 보내줘"
    contacts = FakeSource(
        SourceKind.ADDRESSBOOK_CONTACTS,
        (_candidate(CandidateSpec("contact-daon", SourceKind.ADDRESSBOOK_CONTACTS, "다온-개인", 0.99)),),
    )
    memory = FakeSource(
        SourceKind.HERMES_MEMORY,
        (_candidate(CandidateSpec("memory-daon", SourceKind.HERMES_MEMORY, "다온-연구", 0.98)),),
    )

    # When: candidates are merged only by their canonical value.
    decision = resolve_preflight(_request(raw_text, "다온"), _audit(raw_text), (contacts, memory))

    # Then: distinct canonical targets remain a conflict and raw input is preserved.
    assert decision.decision == DecisionKind.CONFIRMATION_REQUIRED
    assert decision.reason == DecisionReason.CANDIDATE_CONFLICT
    assert decision.request.raw_text == raw_text
    assert decision.selected == ()


def test_requires_confirmation_when_address_book_and_rag_disagree() -> None:
    # Given: address-book and personal-RAG candidates that identify different targets.
    raw_text = "라온에게 메일을 보내줘"
    address_book = FakeSource(
        SourceKind.ADDRESSBOOK_CONTACTS,
        (_candidate(CandidateSpec("contact-raon", SourceKind.ADDRESSBOOK_CONTACTS, "라온-연락처", 0.99)),),
    )
    rag = FakeSource(
        SourceKind.PERSONAL_RAG,
        (_candidate(CandidateSpec("rag-raon", SourceKind.PERSONAL_RAG, "라온-기록", 0.99)),),
    )

    # When: source-weighted candidates enter the policy merger.
    decision = resolve_preflight(_request(raw_text, "라온"), _audit(raw_text), (address_book, rag))

    # Then: independent, credible sources cannot silently choose a recipient.
    assert decision.decision == DecisionKind.CONFIRMATION_REQUIRED
    assert decision.reason == DecisionReason.CANDIDATE_CONFLICT
    assert {candidate.source for candidate in decision.candidates} == {
        SourceKind.ADDRESSBOOK_CONTACTS,
        SourceKind.PERSONAL_RAG,
    }


def test_requires_confirmation_when_every_source_returns_an_explicit_empty_result() -> None:
    # Given: completed local lookups with no candidates.
    raw_text = "나래에게 메일을 보내줘"
    source = FakeSource(SourceKind.PERSONAL_RAG, ())

    # When: the resolver receives the explicit empty tuple.
    decision = resolve_preflight(_request(raw_text, "나래"), _audit(raw_text), (source,))

    # Then: empty is distinct from success and preserves the raw text for ENTITY-CLARIFY.
    assert decision.decision == DecisionKind.CONFIRMATION_REQUIRED
    assert decision.reason == DecisionReason.NO_CANDIDATE
    assert decision.request.raw_text == raw_text


def test_propagates_data_source_failure_instead_of_treating_it_as_empty() -> None:
    # Given: an injected source that could not complete its local lookup.
    raw_text = "이든에게 메일을 보내줘"
    source = FailingSource(SourceKind.HERMES_MEMORY)

    # When / Then: failure is an explicit typed state, never a silent no-candidate result.
    with pytest.raises(DataSourceFailure) as raised:
        resolve_preflight(_request(raw_text, "이든"), _audit(raw_text), (source,))

    assert raised.value.source == SourceKind.HERMES_MEMORY


@pytest.mark.parametrize("surface", ("가람", "노을", "다빈", "루미", "마루"))
def test_resolver_makes_zero_network_calls_for_every_injected_fixture(
    surface: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a network trap and an injected in-memory data source.
    attempts: list[str] = []

    def block_network(*args: str, **kwargs: str) -> None:
        attempts.append("socket.create_connection")
        raise AssertionError("resolver must not access a network")

    monkeypatch.setattr(socket, "create_connection", block_network)
    raw_text = f"{surface}에게 메일을 보내줘"
    source = FakeSource(
        SourceKind.PERSONAL_RAG,
        (_candidate(CandidateSpec(f"rag-{surface}", SourceKind.PERSONAL_RAG, surface, 0.99)),),
    )

    # When: resolution runs over arbitrary synthetic personal-name fixtures.
    decision = resolve_preflight(_request(raw_text, surface), _audit(raw_text), (source,))

    # Then: all behavior is local and injected; the network trap was never touched.
    assert decision.decision == DecisionKind.AUTO_SELECTED
    assert attempts == []
