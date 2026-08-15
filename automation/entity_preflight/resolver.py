"""Offline personal-entity detection and source-injected preflight resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .contracts import (
    AuditMetadata,
    CandidateResolver,
    DetectedEntity,
    EntityKind,
    PreflightDecision,
    PreflightInput,
    RelationshipQuery,
    SourceKind,
)
from .policy import PreflightPolicy, decide, load_policy


_HANGUL_BASE: Final = 0xAC00
_HANGUL_LAST: Final = 0xD7A3
_JUNGSEONG_COUNT: Final = 21
_JONGSEONG_COUNT: Final = 28
_IEUNG: Final = 11
_HIEUH: Final = 18
_W_VOWEL_PAIRS: Final[tuple[tuple[int, int], ...]] = ((9, 0), (10, 1), (14, 5), (15, 6))


@dataclass(frozen=True, slots=True)
class DataSourceFailure(RuntimeError):
    """A local candidate source failed; it must not be represented as an empty result."""

    source: SourceKind
    reason: str

    def __str__(self) -> str:
        return f"entity candidate source failed: {self.source.value}: {self.reason}"


def detect_entities(
    raw_text: str,
    entity_hints: Mapping[str, EntityKind],
) -> tuple[DetectedEntity, ...]:
    """Detect local, caller-hinted personal names in a flattened external-write payload."""
    spans: list[tuple[int, int, str, EntityKind]] = []
    for surface, entity_kind in sorted(entity_hints.items(), key=lambda item: (-len(item[0]), item[0])):
        if not surface:
            continue
        position = raw_text.find(surface)
        while position >= 0:
            spans.append((position, position + len(surface), surface, entity_kind))
            position = raw_text.find(surface, position + len(surface))
    ordered = sorted(spans, key=lambda item: (item[0], -item[1], item[2], item[3].value))
    selected: list[tuple[int, int, str, EntityKind]] = []
    for span in ordered:
        if not selected or span[0] >= selected[-1][1]:
            selected.append(span)
    return tuple(
        DetectedEntity(f"m-{index}", surface, entity_kind, start, end)
        for index, (start, end, surface, entity_kind) in enumerate(selected, 1)
    )


def pronunciation_variants(surface: str) -> tuple[str, ...]:
    """Generate bounded Korean speech-transcription variants without remote lookup."""
    variants = [surface]
    folded = surface.casefold()
    if folded != surface:
        variants.append(folded)
    for index, character in enumerate(surface):
        for replacement in _syllable_variants(character):
            candidate = surface[:index] + replacement + surface[index + 1 :]
            if candidate not in variants:
                variants.append(candidate)
    return tuple(variants)


def rewrite_relationship_queries(request: PreflightInput) -> tuple[RelationshipQuery, ...]:
    """Attach raw, pronunciation, relation, and action signals to each local lookup query."""
    mentions = {entity.mention_id: entity for entity in request.entities}
    return tuple(
        RelationshipQuery(
            query.query_id,
            query.subject_mention_id,
            query.relation,
            query.target_kind,
            _rewritten_question(request, mentions[query.subject_mention_id], query),
        )
        for query in request.relationship_queries
    )


@dataclass(frozen=True, slots=True)
class PersonalEntityResolver:
    """Resolve through injected local sources and delegate scoring and merging to policy."""

    sources: tuple[CandidateResolver, ...]
    policy: PreflightPolicy

    def resolve(self, request: PreflightInput, audit: AuditMetadata) -> PreflightDecision:
        """Run every local source, propagating failures instead of treating them as empty results."""
        rewritten_queries = rewrite_relationship_queries(request)
        queries: tuple[RelationshipQuery | None, ...] = rewritten_queries or (None,)
        candidates = tuple(
            candidate
            for source in self.sources
            for query in queries
            for candidate in source.resolve(request, query)
        )
        return decide(request, candidates, audit, self.policy)


def resolve_preflight(
    request: PreflightInput,
    audit: AuditMetadata,
    sources: tuple[CandidateResolver, ...],
) -> PreflightDecision:
    """Resolve a preflight request using the immutable configured policy seed."""
    return PersonalEntityResolver(sources, load_policy()).resolve(request, audit)


def _rewritten_question(
    request: PreflightInput,
    entity: DetectedEntity,
    query: RelationshipQuery,
) -> str:
    variants = "|".join(pronunciation_variants(entity.surface))
    return (
        f"raw_name={entity.surface}; pronunciation_variants={variants}; "
        f"relation={query.relation}; target_kind={query.target_kind.value}; "
        f"action={request.operation}; target_system={request.target_system}; "
        f"question={query.question}"
    )


def _syllable_variants(character: str) -> tuple[str, ...]:
    codepoint = ord(character)
    if not _HANGUL_BASE <= codepoint <= _HANGUL_LAST:
        return ()
    offset = codepoint - _HANGUL_BASE
    lead, remainder = divmod(offset, _JUNGSEONG_COUNT * _JONGSEONG_COUNT)
    vowel, tail = divmod(remainder, _JONGSEONG_COUNT)
    variants = [_compose_hangul(lead, alternative, tail) for alternative in _vowel_variants(vowel)]
    if lead == _IEUNG:
        variants.append(_compose_hangul(_HIEUH, vowel, tail))
        variants.extend(_compose_hangul(_HIEUH, alternative, tail) for alternative in _vowel_variants(vowel))
    if lead == _HIEUH:
        variants.append(_compose_hangul(_IEUNG, vowel, tail))
        variants.extend(_compose_hangul(_IEUNG, alternative, tail) for alternative in _vowel_variants(vowel))
    return tuple(dict.fromkeys(variants))


def _vowel_variants(vowel: int) -> tuple[int, ...]:
    return tuple(
        alternative
        for full, reduced in _W_VOWEL_PAIRS
        for alternative in ((reduced,) if vowel == full else (full,) if vowel == reduced else ())
    )


def _compose_hangul(lead: int, vowel: int, tail: int) -> str:
    return chr(_HANGUL_BASE + ((lead * _JUNGSEONG_COUNT + vowel) * _JONGSEONG_COUNT + tail))
