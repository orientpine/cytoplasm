"""Deterministic SI-3 validation and metadata caps for inferred proposals."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

_ALLOWED_KINDS: Final = frozenset({"decision", "principle", "preference"})
_SOURCE_KEY_CITATION: Final = re.compile(r"(?m)^\s*(?:[-*]\s+)?source_key\s*:\s*\S+")


@dataclass(frozen=True, slots=True)
class CandidateValidationError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class AuthorityCapError(Exception):
    authority: str

    def __str__(self) -> str:
        return f"inferred authority {self.authority!r} exceeds the advisory/default cap"


@dataclass(frozen=True, slots=True)
class CandidateMetaError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


class InferredAuthority(StrEnum):
    ADVISORY = "advisory"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    title: str
    authority: str
    kind: str = "principle"
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InferredProposal:
    title: str
    kind: str
    authority: InferredAuthority
    tags: tuple[str, ...]
    body: str
    provenance: Literal["inferred"] = "inferred"
    status: None = None


def _section_content(body: str, heading: str) -> str:
    lines = body.splitlines()
    indexes = tuple(index for index, line in enumerate(lines) if line.strip() == heading)
    if len(indexes) != 1:
        raise CandidateValidationError(f"candidate requires exactly one {heading} section")
    start = indexes[0] + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end]).strip()


def validate_candidate_body(body: str) -> str:
    """Require an attributable Evidence section and a substantive Counterexample."""
    evidence = _section_content(body, "## Evidence")
    if not _SOURCE_KEY_CITATION.search(evidence):
        raise CandidateValidationError("Evidence section requires a source_key citation")
    counterexample = _section_content(body, "## Counterexample")
    if not counterexample:
        raise CandidateValidationError("Counterexample section must not be empty")
    return body


def _parse_authority(authority: str) -> InferredAuthority:
    match authority:
        case "advisory":
            return InferredAuthority.ADVISORY
        case "default":
            return InferredAuthority.DEFAULT
        case _:
            raise AuthorityCapError(authority)


def build_proposal(spec: CandidateSpec, body: str) -> InferredProposal:
    if not spec.title.strip():
        raise CandidateMetaError("inferred proposal title must not be empty")
    if spec.kind not in _ALLOWED_KINDS:
        raise CandidateMetaError(f"inferred proposal kind {spec.kind!r} is not a judgment kind")
    authority = _parse_authority(spec.authority)
    validated_body = validate_candidate_body(body)
    return InferredProposal(
        title=spec.title,
        kind=spec.kind,
        authority=authority,
        tags=spec.tags,
        body=validated_body,
    )
