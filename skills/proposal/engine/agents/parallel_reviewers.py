from __future__ import annotations

import concurrent.futures
import json
import re
import warnings
from typing import Final, cast

from ..contracts.collab_models import ReviewerOpinion
from ..contracts.models import PlanSpec, SectionDraft
from ..contracts.protocols import LLMClient, PMSQuery

MAX_PARALLEL_REVIEWERS: Final[int] = 8
REVIEWER_TIMEOUT_SECONDS: Final[float] = 120.0

_PROSE_LOCATOR_RE: Final[re.Pattern[str]] = re.compile(r"^sec\d+(?:\.claim\d+)?$")


class ReviewerPersona:
    def __init__(self, reviewer_id: str, persona_desc: str, llm: LLMClient) -> None:
        self.reviewer_id = reviewer_id
        self.persona_desc = persona_desc
        self._llm = llm

    def review(
        self,
        drafts: list[SectionDraft],
        planspec: PlanSpec,
        pms: PMSQuery,
    ) -> list[ReviewerOpinion]:
        _ = pms
        prompt = self._build_prompt(drafts, planspec)
        try:
            raw = self._llm.complete(f"reviewer_{self.reviewer_id}", prompt)
        except Exception:  # noqa: BLE001
            return []
        return self._parse_response(raw)

    def _build_prompt(self, drafts: list[SectionDraft], planspec: PlanSpec) -> str:
        drafts_text = "\n\n".join(
            f"[sec{draft.section_id}]\n{draft.body[:500]}"
            for draft in sorted(drafts, key=lambda draft: int(draft.section_id))
        )
        return (
            f"당신은 {self.persona_desc}입니다.\n"
            "아래 R&D 연구계획서 초안을 검토하고 섹션별 의견을 제시하라.\n"
            "각 의견은 'locator: sec<N> 또는 sec<N>.claim<M>'으로 구분하라.\n"
            "ir.* locator는 절대 생성하지 말라.\n\n"
            "[응답 형식] JSON만 출력(코드블록 금지):\n"
            '{"opinions": [{"locator": "sec<N>", "opinion": "<의견>", '
            '"proposed_edit": "<수정안 또는 null>"}, ...]}\n\n'
            f"[계획명]\n{planspec.title}\n\n"
            f"[초안]\n{drafts_text}"
        )

    def _parse_response(self, raw: str) -> list[ReviewerOpinion]:
        try:
            loaded = cast("object", json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(loaded, dict):
            return []

        entries = cast("dict[str, object]", loaded).get("opinions")
        if not isinstance(entries, list):
            return []

        opinions: list[ReviewerOpinion] = []
        for entry in cast("list[object]", entries):
            if not isinstance(entry, dict):
                continue
            opinion = self._parse_opinion_entry(cast("dict[str, object]", entry))
            if opinion is not None:
                opinions.append(opinion)
        return opinions

    def _parse_opinion_entry(self, item: dict[str, object]) -> ReviewerOpinion | None:
        locator = str(item.get("locator", "")).strip()
        opinion_text = str(item.get("opinion", "")).strip()
        proposed_edit = _proposed_edit_value(item.get("proposed_edit"))
        if _PROSE_LOCATOR_RE.match(locator) is None or not opinion_text:
            return None
        return ReviewerOpinion(
            reviewer_id=self.reviewer_id,
            locator=locator,
            opinion_text=opinion_text,
            proposed_edit=proposed_edit,
            source_ids=None,
        )


def _proposed_edit_value(value: object) -> str | None:
    if value is None or value == "null":
        return None
    proposed_edit = str(value).strip()
    return proposed_edit or None


def run_parallel_reviewers(
    personas: list[ReviewerPersona],
    drafts: list[SectionDraft],
    planspec: PlanSpec,
    pms: PMSQuery,
    *,
    max_workers: int | None = None,
    timeout: float | None = REVIEWER_TIMEOUT_SECONDS,
) -> list[ReviewerOpinion]:
    if not personas:
        return []

    requested_workers = max_workers if max_workers is not None else len(personas)
    workers = max(1, min(requested_workers, len(personas), MAX_PARALLEL_REVIEWERS))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        future_to_persona = {
            executor.submit(persona.review, drafts, planspec, pms): persona for persona in personas
        }
        done, pending = concurrent.futures.wait(
            future_to_persona,
            timeout=timeout,
            return_when=concurrent.futures.ALL_COMPLETED,
        )

        all_opinions: list[ReviewerOpinion] = []
        for future in done:
            persona = future_to_persona[future]
            try:
                all_opinions.extend(future.result())
            except Exception as exc:  # noqa: BLE001
                warnings.warn(
                    f"Reviewer {persona.reviewer_id} raised {type(exc).__name__}: {exc}",
                    stacklevel=2,
                )

        for future in pending:
            persona = future_to_persona[future]
            future.cancel()
            warnings.warn(
                f"Reviewer {persona.reviewer_id} timed out; using empty opinions.",
                stacklevel=2,
            )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return sorted(all_opinions, key=lambda opinion: (opinion.reviewer_id, opinion.locator))


__all__ = [
    "MAX_PARALLEL_REVIEWERS",
    "REVIEWER_TIMEOUT_SECONDS",
    "ReviewerPersona",
    "run_parallel_reviewers",
]
