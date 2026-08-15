"""In-conversation clarify rendering for an unresolved personal proper noun.

Owner decision (RTS Q3): when the resolver cannot pick a single high-confidence
candidate, the system does **not** open an owner-approval surface. No approval
record is created, no reaction is polled, no watcher is involved. The calling
CLI prints :func:`render_clarify` and exits :data:`ENTITY_CLARIFY_EXIT_CODE`
(non-zero, nothing written) — the same shape as the ``ROUTING-CLARIFY`` exit
in ``skills/calendar/scripts/calendar_cli.py``. The owner answers in the same
conversation turn.

The external write itself keeps going through the existing owner approval gate,
unchanged; this module never touches it.

The rendered text is owner-facing conversation output, not a log record. It may
repeat surfaces and display values the owner already sees, but never a private
``source_ref`` and never the raw request text. General operational logs still
take only ``audit.operational_event``.
"""

from __future__ import annotations

from typing import Final

from .contracts import DecisionReason, PreflightDecision, ResolutionCandidate

ENTITY_CLARIFY_MARKER: Final = "ENTITY-CLARIFY"
ENTITY_CLARIFY_EXIT_CODE: Final = 6

_REASON_LABEL: Final[dict[DecisionReason, str]] = {
    DecisionReason.CANDIDATE_CONFLICT: "서로 다른 후보가 경합합니다",
    DecisionReason.LOW_CONFIDENCE: "가장 유력한 후보도 자동 선택 하한에 못 미칩니다",
    DecisionReason.NO_CANDIDATE: "어떤 후보원에서도 값을 찾지 못했습니다",
}
_FALLBACK_LABEL: Final = "고유명사를 확정하지 못했습니다"


def _candidate_line(index: int, candidate: ResolutionCandidate) -> str:
    return (
        f"  {index}) {candidate.display_value} "
        f"[source={candidate.source.value} confidence={candidate.confidence:.2f}]"
    )


def _mention_block(decision: PreflightDecision, mention_id: str) -> list[str]:
    entity = next(item for item in decision.request.entities if item.mention_id == mention_id)
    header = f"- '{entity.surface}'({entity.entity_kind.value}) 후보:"
    candidates = sorted(
        (item for item in decision.candidates if item.mention_id == mention_id),
        key=lambda item: (-item.confidence, item.normalized_value, item.candidate_id),
    )
    if not candidates:
        return [header, "  없음 — 정규화 값을 직접 알려주세요."]
    return [header, *(_candidate_line(rank, item) for rank, item in enumerate(candidates, 1))]


def render_clarify(decision: PreflightDecision) -> str:
    """Render the deterministic owner-facing clarify text for one decision.

    Fail-closed: a decision that already resolved every mention has nothing to
    clarify, and asking for one is a caller bug.
    """
    if not decision.needs_confirmation:
        raise ValueError("decision needs no clarification")
    resolved = {value.mention_id for value in decision.selected}
    unresolved = [
        entity.mention_id
        for entity in decision.request.entities
        if entity.mention_id not in resolved
    ]
    label = _REASON_LABEL.get(decision.reason, _FALLBACK_LABEL)
    lines = [
        f"{ENTITY_CLARIFY_MARKER} {label} — 외부 쓰기를 시작하지 않았습니다.",
        f"reason={decision.reason.value} correlation_id={decision.audit.correlation_id}",
        *(line for mention_id in unresolved for line in _mention_block(decision, mention_id)),
        "이 대화에서 맞는 값을 알려주세요 (승인 리액션이 아니라 대화 답변으로 확정합니다).",
    ]
    return "\n".join(lines)
