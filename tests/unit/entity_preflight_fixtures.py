"""Typed loader for the data-driven entity-preflight regression fixtures.

Each JSON file under ``fixtures/entity_preflight/`` is exactly one regression
case, and discovery is a plain directory listing: dropping in one more file
adds one more parametrized case, with no code change anywhere. The loader is
deliberately strict — a missing, malformed, or self-contradictory fixture
raises :class:`FixtureError` loudly instead of being skipped, so silent
non-discovery is impossible.

Schema (``schema_version`` 1)::

    case_id      str   must equal the file stem
    category     str   regression class
    synthetic    bool  must be true; every name in the file is invented
    description  str   what the case locks down
    request      {request_id, raw_text, target_system, operation}
    detection    {entities: [{mention_id, surface, entity_kind}],
                  relationship_queries: [{query_id, subject_mention_id,
                                          relation, target_kind, question}]}
    candidates   [{candidate_id, mention_id, source, normalized_value,
                   display_value, confidence, source_ref,
                   relationship_query_id}]
    expected     {auto_normalization, needs_confirmation, decision, reason,
                  selected: {mention_id: normalized_value},
                  clarify_mentions: [mention_id]}

Entity spans are derived, not declared: the surface must occur exactly once in
``raw_text``, and ``PreflightInput`` validates the resulting span itself.

Candidates reach the resolver through :class:`FixtureCandidateResolver`, which
implements the published ``CandidateResolver`` protocol — the same boundary the
production personal-RAG, memory, and address-book adapters implement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from entity_preflight_fixture_json import (
    FixtureError,
    JsonObject,
    as_object,
    enum_value,
    field,
    flag,
    mapping,
    number,
    optional_text,
    rows,
    strings,
    text,
)

from automation.entity_preflight.contracts import (
    CandidateResolver,
    DecisionKind,
    DecisionReason,
    DetectedEntity,
    EntityKind,
    PreflightInput,
    RelationshipQuery,
    ResolutionCandidate,
    SourceKind,
)

FIXTURE_DIR: Final = Path(__file__).resolve().parent / "fixtures" / "entity_preflight"
SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class ExpectedOutcome:
    """The fixture's declared expectation, stated in published-contract terms."""

    auto_normalization: bool
    needs_confirmation: bool
    decision: DecisionKind
    reason: DecisionReason
    selected: dict[str, str]
    clarify_mentions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FixtureCase:
    """One regression case: input text, detection target, candidates, outcome."""

    case_id: str
    category: str
    description: str
    path: Path
    request: PreflightInput
    candidates: tuple[ResolutionCandidate, ...]
    expected: ExpectedOutcome


def _entity(payload: JsonObject, raw_text: str, path: Path) -> DetectedEntity:
    surface = text(payload, "surface", path)
    if raw_text.count(surface) != 1:
        raise FixtureError(f"{path.name}: surface '{surface}' must occur exactly once in raw_text")
    start = raw_text.index(surface)
    return DetectedEntity(
        mention_id=text(payload, "mention_id", path),
        surface=surface,
        entity_kind=enum_value(EntityKind, text(payload, "entity_kind", path), path),
        start=start,
        end=start + len(surface),
    )


def _query(payload: JsonObject, path: Path) -> RelationshipQuery:
    return RelationshipQuery(
        query_id=text(payload, "query_id", path),
        subject_mention_id=text(payload, "subject_mention_id", path),
        relation=text(payload, "relation", path),
        target_kind=enum_value(EntityKind, text(payload, "target_kind", path), path),
        question=text(payload, "question", path),
    )


def _candidate(payload: JsonObject, path: Path) -> ResolutionCandidate:
    return ResolutionCandidate(
        candidate_id=text(payload, "candidate_id", path),
        mention_id=text(payload, "mention_id", path),
        source=enum_value(SourceKind, text(payload, "source", path), path),
        normalized_value=text(payload, "normalized_value", path),
        display_value=text(payload, "display_value", path),
        confidence=number(payload, "confidence", path),
        source_ref=text(payload, "source_ref", path),
        relationship_query_id=optional_text(payload, "relationship_query_id", path),
    )


def _request(fixture: JsonObject, path: Path) -> PreflightInput:
    payload = mapping(fixture, "request", path)
    detection = mapping(fixture, "detection", path)
    raw_text = text(payload, "raw_text", path)
    return PreflightInput(
        request_id=text(payload, "request_id", path),
        raw_text=raw_text,
        target_system=text(payload, "target_system", path),
        operation=text(payload, "operation", path),
        entities=tuple(_entity(row, raw_text, path) for row in rows(detection, "entities", path)),
        relationship_queries=tuple(
            _query(row, path) for row in rows(detection, "relationship_queries", path)
        ),
    )


def _expected(fixture: JsonObject, path: Path) -> ExpectedOutcome:
    payload = mapping(fixture, "expected", path)
    selected = mapping(payload, "selected", path)
    if any(not isinstance(value, str) or not value for value in selected.values()):
        raise FixtureError(f"{path.name}: 'selected' values must be non-empty strings")
    return ExpectedOutcome(
        auto_normalization=flag(payload, "auto_normalization", path),
        needs_confirmation=flag(payload, "needs_confirmation", path),
        decision=enum_value(DecisionKind, text(payload, "decision", path), path),
        reason=enum_value(DecisionReason, text(payload, "reason", path), path),
        selected={key: str(value) for key, value in selected.items()},
        clarify_mentions=strings(payload, "clarify_mentions", path),
    )


def _check_coherence(case: FixtureCase) -> None:
    """A fixture that contradicts itself is a bug in the fixture, not a case."""
    expected = case.expected
    name = case.path.name
    mentions = {entity.mention_id for entity in case.request.entities}
    if expected.needs_confirmation != (expected.decision == DecisionKind.CONFIRMATION_REQUIRED):
        raise FixtureError(f"{name}: needs_confirmation disagrees with decision")
    if expected.auto_normalization != (expected.decision == DecisionKind.AUTO_SELECTED):
        raise FixtureError(f"{name}: auto_normalization disagrees with decision")
    if bool(expected.clarify_mentions) != expected.needs_confirmation:
        raise FixtureError(f"{name}: clarify_mentions must be listed exactly when confirming")
    if not set(expected.selected) | set(expected.clarify_mentions) <= mentions:
        raise FixtureError(f"{name}: expectation names a mention that was never detected")
    if expected.decision == DecisionKind.AUTO_SELECTED and set(expected.selected) != mentions:
        raise FixtureError(f"{name}: an auto-selected case must resolve every mention")


def load_case(path: Path) -> FixtureCase:
    """Load and validate one fixture file. Anything unusable raises loudly."""
    fixture = as_object(json.loads(path.read_text(encoding="utf-8")), path, "the fixture")
    if field(fixture, "schema_version", path) != SCHEMA_VERSION:
        raise FixtureError(f"{path.name}: schema_version must be {SCHEMA_VERSION}")
    if not flag(fixture, "synthetic", path):
        raise FixtureError(f"{path.name}: every fixture must declare synthetic data")
    case_id = text(fixture, "case_id", path)
    if case_id != path.stem:
        raise FixtureError(f"{path.name}: case_id '{case_id}' must equal the file name stem")
    case = FixtureCase(
        case_id=case_id,
        category=text(fixture, "category", path),
        description=text(fixture, "description", path),
        path=path,
        request=_request(fixture, path),
        candidates=tuple(_candidate(row, path) for row in rows(fixture, "candidates", path)),
        expected=_expected(fixture, path),
    )
    _check_coherence(case)
    return case


def fixture_paths(directory: Path = FIXTURE_DIR) -> tuple[Path, ...]:
    """List the fixture files on disk. This is the only discovery mechanism."""
    if not directory.is_dir():
        raise FixtureError(f"fixture directory is missing: {directory}")
    return tuple(sorted(directory.glob("*.json")))


def discover_cases(directory: Path = FIXTURE_DIR) -> tuple[FixtureCase, ...]:
    """Load every fixture file in ``directory``, one case per file."""
    paths = fixture_paths(directory)
    if not paths:
        raise FixtureError(f"no fixture files found under {directory}")
    cases = tuple(load_case(path) for path in paths)
    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise FixtureError(f"duplicate fixture case ids under {directory}")
    return cases


@dataclass(frozen=True, slots=True)
class FixtureCandidateResolver:
    """Fixture-backed adapter satisfying the published ``CandidateResolver``.

    One instance per source, exactly as the guide's call flow registers one
    adapter per local candidate source. It answers from the fixture file only,
    so no case can reach a network or a real personal store.
    """

    source: SourceKind
    case: FixtureCase

    def resolve(
        self,
        request: PreflightInput,
        query: RelationshipQuery | None,
    ) -> tuple[ResolutionCandidate, ...]:
        mentions = {entity.mention_id for entity in request.entities}
        query_id = None if query is None else query.query_id
        return tuple(
            candidate
            for candidate in self.case.candidates
            if candidate.source == self.source
            and candidate.relationship_query_id == query_id
            and candidate.mention_id in mentions
        )


def fixture_sources(case: FixtureCase) -> tuple[CandidateResolver, ...]:
    """Register one fixture-backed adapter per published candidate source."""
    return tuple(FixtureCandidateResolver(source=source, case=case) for source in SourceKind)
