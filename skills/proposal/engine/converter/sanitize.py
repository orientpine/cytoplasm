"""G_sanitize gate — second-barrier leak detection.

Asserts that rendered artifact text contains ZERO verbatim strings
from unverified-source evidence units.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..contracts import CitationStatus

# Minimum substring length to check for verbatim leaks
_MIN_CHUNK = 20

@dataclass
class SanitizeViolation:
    span: str       # the matched substring found in artifact_text
    source_id: str  # unit_id of the leaking evidence unit
    reason: str     # human-readable explanation


@dataclass
class SanitizeReport:
    ok: bool
    violations: list[SanitizeViolation] = field(default_factory=list)


def sanitize_gate(artifact_text: str, pms: Any) -> SanitizeReport:  # noqa: ANN401
    """Check *artifact_text* for verbatim leaks from unverified-source units.

    Parameters
    ----------
    artifact_text:
        The fully-rendered proposal text to audit.
    pms:
        A ProposalMaterialStore (or any object satisfying PMSQuery protocol).
        If it exposes ``get_citation_status``, that is used; otherwise units
        absent from ``public_evidence()`` are treated as INTERNAL.
    """
    violations: list[SanitizeViolation] = []

    # Determine citation-status resolver
    _get_status = _build_status_resolver(pms)

    # Collect all buckets present in the store
    # We iterate over every unit via public_evidence + evidence_for_bucket.
    # Since we don't know bucket names upfront, we use a two-pass approach:
    # first collect all units via public_evidence (PUBLIC ones), then we need
    # the non-public ones.  The concrete store exposes _by_bucket; we avoid
    # private access by using a known-bucket enumeration trick:
    # call evidence_for_bucket for every bucket we can discover.
    all_units = _collect_all_units(pms)

    for unit in all_units:
        status = _get_status(unit.unit_id)
        if status == CitationStatus.PUBLIC:
            continue

        # This unit is not citation-verified — check for verbatim leaks
        for provenance in unit.provenances:
            verbatim = provenance.verbatim
            if not verbatim:
                continue

            # 1. Full verbatim match (if ≥ MIN_CHUNK chars)
            if len(verbatim) >= _MIN_CHUNK and verbatim in artifact_text:
                violations.append(
                    SanitizeViolation(
                        span=verbatim,
                        source_id=unit.unit_id,
                        reason=f"Full verbatim from {status.value} unit leaked into artifact",
                    )
                )
                continue  # no need to check chunks separately

            # 2. Chunk-based substring matching (≥ MIN_CHUNK chars each)
            chunks = _sliding_chunks(verbatim, _MIN_CHUNK)
            for chunk in chunks:
                if chunk in artifact_text:
                    violations.append(
                        SanitizeViolation(
                            span=chunk,
                            source_id=unit.unit_id,
                            reason=(
                                f"Verbatim chunk ({len(chunk)} chars) from "
                                f"{status.value} unit leaked into artifact"
                            ),
                        )
                    )
                    break  # one violation per unit is enough

    if violations:
        return SanitizeReport(ok=False, violations=violations)
    return SanitizeReport(ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_all_units(pms: Any) -> list[Any]:  # noqa: ANN401
    """Return every EvidenceUnit in the store regardless of citation status.

    Strategy:
    - Start with public_evidence() to discover bucket names.
    - Then call evidence_for_bucket() for each discovered bucket to get ALL
      units (including unverified-source).
    - Also try the private _by_bucket attribute as a fast path if available.
    """
    # Fast path: concrete ProposalMaterialStore exposes _by_bucket
    by_bucket = getattr(pms, "_by_bucket", None)
    if by_bucket is not None:
        seen: set[str] = set()
        result = []
        for units in by_bucket.values():
            for u in units:
                if u.unit_id not in seen:
                    seen.add(u.unit_id)
                    result.append(u)
        return result

    # Fallback: discover buckets from public units, then expand
    buckets: set[str] = set()
    seen_ids: set[str] = set()
    all_units = []

    for unit in pms.public_evidence():
        buckets.add(unit.bucket)
        if unit.unit_id not in seen_ids:
            seen_ids.add(unit.unit_id)
            all_units.append(unit)

    for bucket in buckets:
        for unit in pms.evidence_for_bucket(bucket):
            if unit.unit_id not in seen_ids:
                seen_ids.add(unit.unit_id)
                all_units.append(unit)

    return all_units


def _build_status_resolver(pms: Any) -> Callable[[str], CitationStatus]:  # noqa: ANN401
    resolver = getattr(pms, "get_citation_status", None)
    if resolver is not None:
        return resolver  # type: ignore[return-value]

    public_ids = {u.unit_id for u in pms.public_evidence()}

    def _fallback(unit_id: str) -> CitationStatus:
        return CitationStatus.PUBLIC if unit_id in public_ids else CitationStatus.INTERNAL

    return _fallback


def _sliding_chunks(text: str, min_len: int) -> list[str]:
    """Return non-overlapping chunks of *text* each of length *min_len*.

    Only the last chunk may be shorter (and is discarded if < min_len).
    """
    if len(text) < min_len:
        return []
    return [text[i : i + min_len] for i in range(0, len(text) - min_len + 1, min_len)]


__all__ = ["SanitizeReport", "SanitizeViolation", "sanitize_gate"]
