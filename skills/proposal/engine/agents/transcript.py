from __future__ import annotations

import json
from pathlib import Path

from ..contracts.collab_models import (
    MergeDecision,
    MergePlan,
    ReviewerOpinion,
    RevisionTranscript,
    SectionOpinionEntry,
    TranscriptSection,
)

RESULT_PREVIEW_LIMIT = 300


def build_transcript(
    opinions: list[ReviewerOpinion],
    plan: MergePlan,
    decisions: list[MergeDecision],
    *,
    plan_name: str = "",
) -> RevisionTranscript:
    """Build a frozen revision transcript from reviewer opinions and merge decisions."""
    opinions_by_locator: dict[str, list[ReviewerOpinion]] = {}
    for opinion in opinions:
        opinions_by_locator.setdefault(opinion.locator, []).append(opinion)

    decision_by_hunk: dict[str, MergeDecision] = {decision.hunk_id: decision for decision in decisions}
    sections: list[TranscriptSection] = []

    for hunk in plan.hunks:
        decision = decision_by_hunk.get(hunk.hunk_id)
        chosen_reviewer = decision.chosen if decision is not None else None
        opinion_entries = tuple(
            SectionOpinionEntry(
                reviewer_id=opinion.reviewer_id,
                opinion_text=opinion.opinion_text,
                chosen=opinion.reviewer_id == chosen_reviewer,
            )
            for opinion in sorted(
                opinions_by_locator.get(hunk.locator, []),
                key=lambda item: item.reviewer_id,
            )
        )

        if decision is None:
            aggregated = f"미결정 (hunk_id={hunk.hunk_id!r})"
            result_text = hunk.base_text
            unresolved = True
        elif decision.chosen == "base":
            aggregated = "원본 유지 (모든 제안 기각)"
            result_text = hunk.base_text
            unresolved = False
        elif decision.chosen == "custom":
            aggregated = "커스텀 결정 채택"
            result_text = decision.final_text
            unresolved = False
        else:
            aggregated = f"{decision.chosen} 리뷰어 의견 채택"
            result_text = decision.final_text
            unresolved = False

        sections.append(
            TranscriptSection(
                locator=hunk.locator,
                opinions=opinion_entries,
                aggregated=aggregated,
                result_text=result_text,
                unresolved=unresolved,
            )
        )

    return RevisionTranscript(
        sections=tuple(sorted(sections, key=lambda section: section.locator)),
        plan_name=plan_name,
    )


def save_transcript_json(transcript: RevisionTranscript, path: str | Path) -> None:
    """Save a revision transcript as deterministic UTF-8 JSON."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        transcript.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    out_path.write_text(f"{payload}\n", encoding="utf-8")


def save_transcript_md(transcript: RevisionTranscript, path: str | Path) -> None:
    """Save a revision transcript as human-readable UTF-8 Markdown."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        f"# 개정 트랜스크립트: {transcript.plan_name or '(unnamed)'}",
        "",
    ]

    for section in transcript.sections:
        lines.append(f"## {section.locator}")
        if section.unresolved:
            lines.append("> ⚠️ 미결정")
        lines.append("")

        if section.opinions:
            lines.append("### 리뷰어 의견")
            for entry in section.opinions:
                marker = "✅" if entry.chosen else "◻️"
                lines.append(f"- **{entry.reviewer_id}** {marker}: {entry.opinion_text}")
            lines.append("")

        lines.append("### 취합 결정")
        lines.append(section.aggregated)
        lines.append("")

        result_text = section.result_text
        preview = result_text[:RESULT_PREVIEW_LIMIT]
        suffix = "..." if len(result_text) > RESULT_PREVIEW_LIMIT else ""
        lines.append("### 결과")
        lines.append(f"{preview}{suffix}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


__all__ = ["build_transcript", "save_transcript_json", "save_transcript_md"]
