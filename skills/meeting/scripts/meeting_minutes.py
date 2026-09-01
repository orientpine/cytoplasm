"""회의록 문서 렌더러 — 연구 회의록 골격 + 근거 하단 배치.

레이아웃 규약(소유자 지시 2026-08-26, 근거는 `.omo/plans/meeting-minutes-redesign.md`):

- 본문은 스캔 가능해야 한다. 메타데이터 헤더 → 한눈에 보기 → 결정사항 → 액션 아이템(표)
  → 마일스톤 → 논의 요지 → 미결 → 다음 회의. **비어 있는 조건부 섹션은 생략한다.**
- 근거 원문·지식 파사드 출처·원문 전사본은 전부 `APPENDIX_HEADING` 아래로 내린다.
  본문에는 `[근n]` 마커만 남는다.
- 마커는 `[^n]` 각주 문법이 아니다. 각주는 렌더러별 지원이 갈리고, 지원하는 렌더러는
  정의를 문서 끝으로 옮겨 우리가 만든 제목을 비운다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Sequence

import meeting_action_db
import meeting_template
from meeting_llm import ActionItem, Extraction

APPENDIX_HEADING: Final = "## 부록 · 근거와 원문"
APPENDIX_LEAD: Final = "> 여기부터는 근거 자료입니다. 회의록 본문은 위에서 끝납니다."
#: `### C. 원문 전사본` 바로 아래에 놓인다. 전사본은 음성 인식 결과라 고유명사가 틀린다 —
#: 2026-08-26 실측으로 전사본의 '한정기술'(정본은 한국전력기술)이 외부 배포용 공정표
#: 템플릿에 그대로 실렸다. 금지를 유혹이 있는 바로 그 자리에 적는다.
TRANSCRIPT_WARNING: Final = (
    "> ⚠ 아래는 음성 인식 전사본입니다. 고유명사·용어에 오기가 있습니다. "
    "공정표·계획서·보고서 등 산출물의 출처로 쓰지 마십시오 — "
    "산출물은 부록 위의 본문에서만 작성합니다."
)
_NO_DEADLINE: Final = "—"
_MINE: Final = "**나**"


def source_block(note_name: str, project: str) -> str:
    """Where the reader should go for truth, most authoritative first.

    Without a project we do not invent a Drive path; the local note is all we know.
    With one, the published minutes lead — the local copy carries the raw transcript
    in its appendix, and an author who opens it first ends up quoting speech-to-text.
    """
    local = f"~/notes/meetings/{note_name}"
    if not project:
        return f"\n출처: {local}"
    return (
        f"\n출처(정본): Drive 회의록/{project}/ — 산출물은 이 정본에서만 작성합니다"
        f"\n로컬 사본: {local} (부록에 전사본 포함 — 산출물 출처로 쓰지 않음)"
    )


def finalized_view(text: str) -> str:
    """The part of a rendered note an artifact may be authored from.

    Everything from `APPENDIX_HEADING` down is source material — cited basis lines and
    the raw transcript — not a statement of what the meeting concluded. A caller that
    reads the whole file reads speech-to-text output as if it were fact. The boundary
    lives here, next to the heading it depends on, so it cannot drift from a copy.
    """
    index = text.find(APPENDIX_HEADING)
    return text if index < 0 else text[:index].rstrip() + "\n"


def evidence_marker(index: int) -> str:
    return f"[근{index}]"


@dataclass(slots=True)
class _Bases:
    entries: list[str] = field(default_factory=list)

    def mark(self, basis: str) -> str:
        text = " ".join(basis.split())
        if not text:
            return ""
        self.entries.append(text)
        return evidence_marker(len(self.entries))

    def lines(self) -> list[str]:
        """List items, not bare lines — consecutive plain lines collapse into one paragraph."""
        return [
            f"- {evidence_marker(number)} {text}"
            for number, text in enumerate(self.entries, start=1)
        ]


def _yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _cell(value: str) -> str:
    return value.replace("|", "\\|")


def _suffix(marker: str) -> str:
    return f" {marker}" if marker else ""


def _frontmatter(*, label: str, sensitive: bool, stamp: str) -> list[str]:
    tags = ["meeting", "w2-3"] + (["patent-sensitive"] if sensitive else [])
    return [
        "---",
        f"title: {_yaml_str(f'회의: {label}')}",
        f"tags: [{', '.join(tags)}]",
        f"created: {stamp}",
        f"updated: {stamp}",
        "links: []",
        "---",
        "",
    ]


def _header(
    *,
    label: str,
    kind: str,
    extraction: Extraction,
    sensitive: bool,
    ref: str,
    now: datetime,
    slide_notes: tuple[str, ...],
    reference_notes: tuple[str, ...] = (),
) -> list[str]:
    meeting = extraction.meeting
    heading = label if sensitive else (meeting.title.strip() or label)
    rows = [("일시", meeting.date or now.strftime("%Y-%m-%d"))]
    for name, value in (("장소", meeting.place), ("참석", ", ".join(meeting.attendees)),
                        ("발표자료", ", ".join(slide_notes)), ("참고자료", ", ".join(reference_notes))):
        if value:
            rows.append((name, value))
    rows.append(("원본", f"{kind} · `{ref}`"))
    rows.append(("작성", f"{now.strftime('%Y-%m-%d %H:%M')} KST · meeting 스킬"))
    lines = [f"# {heading}", "", "| 항목 | 내용 |", "| --- | --- |"]
    lines += [f"| {name} | {_cell(value)} |" for name, value in rows]
    lines.append("")
    return lines


def _summary(extraction: Extraction) -> list[str]:
    if not extraction.summary:
        return []
    return ["## 한눈에 보기", "", *[f"- {line}" for line in extraction.summary], ""]


def _decisions(extraction: Extraction, bases: _Bases) -> list[str]:
    lines = ["## 결정사항", ""]
    if not extraction.decisions:
        lines += ["- 없음", ""]
        return lines
    for number, decision in enumerate(extraction.decisions, start=1):
        lines.append(f"{number}. {decision.text}{_suffix(bases.mark(decision.basis))}")
    lines.append("")
    return lines


def _action_row(item: ActionItem, owner: str, bases: _Bases) -> str:
    marker = bases.mark(item.basis)
    return (
        f"| {owner} | {_cell(item.title)} | {item.deadline or _NO_DEADLINE} "
        f"| {marker or _NO_DEADLINE} |"
    )


def _actions(extraction: Extraction, bases: _Bases) -> list[str]:
    lines = ["## 액션 아이템", ""]
    if not extraction.todos and not extraction.others:
        lines += ["- 없음", ""]
        return lines
    lines += ["| 담당 | 할 일 | 마감 | 근거 |", "| --- | --- | --- | --- |"]
    lines += [_action_row(item, _MINE, bases) for item in extraction.todos]
    lines += [
        _action_row(item, _cell(item.owner or "담당 미정"), bases)
        for item in extraction.others
    ]
    lines.append("")
    return lines


def _milestones(extraction: Extraction, bases: _Bases) -> list[str]:
    if not extraction.milestones:
        return []
    lines = ["## 마일스톤", "", "| 날짜 | 이벤트 | 근거 |", "| --- | --- | --- |"]
    for item in extraction.milestones:
        marker = bases.mark(item.basis)
        lines.append(
            f"| {item.deadline or _NO_DEADLINE} | {_cell(item.title)} "
            f"| {marker or _NO_DEADLINE} |"
        )
    lines.append("")
    return lines


def _discussion(extraction: Extraction, bases: _Bases) -> list[str]:
    if not extraction.discussion:
        return []
    lines = ["## 논의 요지", ""]
    for topic in extraction.discussion:
        subject = " ".join(topic.topic.split())
        lines.append(f"- **{subject}**{_suffix(bases.mark(topic.basis))}")
        lines += [f"  - {point}" for point in topic.points]
        lines.append("")
    return lines


def _open_questions(extraction: Extraction, bases: _Bases) -> list[str]:
    if not extraction.open_questions:
        return []
    lines = ["## 미결·확인 필요", ""]
    for question in extraction.open_questions:
        owner = f" (담당 {question.owner})" if question.owner else ""
        lines.append(f"- {question.title}{owner}{_suffix(bases.mark(question.basis))}")
    lines.append("")
    return lines


def _next_meeting(extraction: Extraction) -> list[str]:
    upcoming = extraction.next_meeting
    if upcoming is None or not (upcoming.when or upcoming.note):
        return []
    detail = f" — {upcoming.note}" if upcoming.note else ""
    return ["## 다음 회의", "", f"- {upcoming.when or '일정 미정'}{detail}", ""]


def _appendix(bases: _Bases, *, evidence_footer: str, original_text: str) -> list[str]:
    lines = [APPENDIX_HEADING, "", APPENDIX_LEAD, "", "### A. 근거 원문", ""]
    lines += bases.lines() or ["- 회의록에서 인용할 근거 문장을 찾지 못했습니다."]
    lines += ["", "### B. 선행 근거", ""]
    lines.append(evidence_footer or "- 선행 근거를 수집하지 않았습니다 (`--with-evidence` 미사용).")
    lines += ["", "### C. 원문 전사본", "", TRANSCRIPT_WARNING, "",
              "```", original_text.rstrip(), "```", ""]
    return lines


def render(
    *,
    label: str,
    kind: str,
    extraction: Extraction,
    original_text: str,
    sensitive: bool,
    ref: str,
    now: datetime,
    evidence_footer: str = "",
    slide_notes: tuple[str, ...] = (),
    reference_notes: tuple[str, ...] = (),
    action_sections: Sequence[str] = (),
    template: "meeting_template.Template | None" = None,
) -> str:
    bases = _Bases()
    header = _header(
        label=label, kind=kind, extraction=extraction, sensitive=sensitive,
        ref=ref, now=now, slide_notes=slide_notes, reference_notes=reference_notes,
    )
    if template is None:
        body = [
            *_summary(extraction), *_decisions(extraction, bases), *_actions(extraction, bases),
            *_milestones(extraction, bases), *_discussion(extraction, bases),
            *_open_questions(extraction, bases), *_next_meeting(extraction), *action_sections,
        ]
    else:
        body, consumed_actions = meeting_template.render_form_body(
            template,
            header=header,
            attendees=extraction.meeting.attendees,
            action_sections=action_sections,
            split_action_sections=meeting_action_db.split_sections,
            summary=lambda: _summary(extraction),
            decisions=lambda: _decisions(extraction, bases) if extraction.decisions else [],
            discussion=lambda: _discussion(extraction, bases),
            actions=lambda: _actions(extraction, bases),
            milestones=lambda: _milestones(extraction, bases),
            open_questions=lambda: _open_questions(extraction, bases),
            next_meeting=lambda: _next_meeting(extraction),
        )
        if not consumed_actions:
            body += action_sections
        header = header[:2]
    lines = [
        *_frontmatter(label=label, sensitive=sensitive, stamp=now.isoformat(timespec="seconds")),
        *header, *body,
        *_appendix(bases, evidence_footer=evidence_footer, original_text=original_text),
    ]
    return "\n".join(lines)
