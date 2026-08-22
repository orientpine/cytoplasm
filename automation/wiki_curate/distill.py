"""Distill one Obsidian note into a wiki draft body.

The LLM leg reuses ``automation.twin_distill.llm`` — same Protocol, same LiteLLM
route, so this package adds no new model surface and no new budget path.
patent-sensitive sources never reach a prompt: they are dropped during candidate
selection, before this module is called.
"""

from __future__ import annotations

from automation.twin_distill.llm import LlmClient
from automation.wiki_curate.candidates import Candidate

_PROMPT = """당신은 소유자의 개인 위키에 들어갈 노트 초안을 만든다.
아래 원천 노트에서 **재사용 가능한 사실·판단만** 남기고 요약하라.

규칙:
- 원문을 그대로 옮기지 말고 압축한다. 원문은 이미 Obsidian 에 있다.
- 원천에 없는 내용을 지어내지 않는다. 불확실하면 쓰지 않는다.
- 마크다운 `## 요약` / `## 근거` 두 절로만 쓴다.

제목: {title}
원천: {ref}
사건 당일: {event_date}

--- 원천 본문 ---
{body}
"""


class DistillRefused(RuntimeError):
    """The model returned nothing usable; refuse rather than draft an empty note."""


def render_prompt(candidate: Candidate) -> str:
    return _PROMPT.format(
        title=candidate.title,
        ref=candidate.source_ref,
        event_date=candidate.event_date or "미상",
        body=candidate.body,
    )


def distilled_body(candidate: Candidate, *, client: LlmClient) -> str:
    reply = client.complete(render_prompt(candidate)).strip()
    if not reply:
        raise DistillRefused(f"empty distillation for {candidate.source_ref}")
    return f"{reply}\n\n## 원천\n- Obsidian: {candidate.source_ref}\n"
