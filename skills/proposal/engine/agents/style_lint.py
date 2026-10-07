from __future__ import annotations

import re
from dataclasses import dataclass

from .kimm_domain import KIMM_DOMAIN, KIMMDomainPack
from ..contracts import SectionDraft
from ..contracts.layout_profile import (
    LEGACY_LAYOUT_PROFILE_NAME,
    LayoutProfile,
    get_layout_profile,
)

__all__ = [
    "ForbiddenExpressionViolation",
    "ForbiddenExpressionsReport",
    "LintReport",
    "LintViolation",
    "check_forbidden_expressions",
    "compress",
    "lint",
]


@dataclass(frozen=True)
class LintViolation:
    rule: str
    span: str
    suggestion: str


@dataclass(frozen=True)
class LintReport:
    ok: bool
    violations: list[LintViolation]
    char_count: int
    budget: int


_POLITE_ENDINGS = (
    "습니다",
    "습니다.",
    "습니까",
    "습니까.",
    "입니다",
    "입니다.",
    "입니까",
    "입니까.",
    "합니다",
    "합니다.",
    "합니까",
    "합니까.",
    "랍니다",
    "랍니다.",
    "니다",
    "니다.",
)
# 개조식 항목은 문장이 아니라 항목이다 — 상위 계층은 명사형으로 끝난다. The bullet
# glyphs come from KIMM_DOMAIN so the linter and the renderer cannot disagree
# about what a bullet looks like.
_ENUMERATED_RE = re.compile(
    r"^\s*(?:[" + "".join(re.escape(glyph) for glyph in KIMM_DOMAIN.bullet_glyphs)
    + r"*•]|\(?\d+(?:\.\d+)*\)?[.)]?)\s+"
)
_GLOSSARY_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("연구목표", "목표", "연구목표/목표 중 하나로 통일하세요."),
    ("방법론", "방법", "방법론/방법 중 하나로 통일하세요."),
)


def lint(
    draft: SectionDraft,
    *,
    profile: LayoutProfile | str = LEGACY_LAYOUT_PROFILE_NAME,
) -> LintReport:
    body = draft.body
    active_profile = (
        profile
        if isinstance(profile, LayoutProfile)
        else get_layout_profile(profile)
    )
    budget = active_profile.prose_budgets[int(draft.section_id)]
    violations: list[LintViolation] = []

    violations.extend(_lint_da_endings(body))
    violations.extend(_lint_glossary(body))

    if len(body) > int(budget * 1.1):
        violations.append(
            LintViolation(
                rule="over_budget",
                span=f"len={len(body)}",
                suggestion=f"{budget}자 이내로 줄이세요.",
            )
        )

    return LintReport(ok=not violations, violations=violations, char_count=len(body), budget=budget)


def compress(draft: SectionDraft, budget: int) -> SectionDraft:
    if len(draft.body) <= budget:
        return draft

    kept = _split_segments(draft.body)
    while kept and len("".join(kept).strip()) > budget:
        _ = kept.pop()

    compressed_body = "".join(kept).strip()
    if len(compressed_body) > budget:
        compressed_body = compressed_body[:budget].rstrip()

    return draft.model_copy(update={"body": compressed_body})


def _split_segments(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[。.\n])", text) if part]


_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s.*$", re.MULTILINE)


def _prose_only(text: str) -> str:
    """Drop the markdown structure the renderer consumes, keeping the prose.

    A heading is not a sentence, and splitting "## 1-1. 기술적 배경" on its period
    produces two fragments that can never satisfy the -다 rule.
    """
    return _MARKDOWN_HEADING_RE.sub("", text)


def _split_sentences(text: str) -> list[str]:
    return [part for part in _split_segments(_prose_only(text)) if part.strip()]


def _lint_da_endings(body: str) -> list[LintViolation]:
    violations: list[LintViolation] = []
    for raw_sentence in _split_sentences(body):
        sentence = raw_sentence.strip()
        if not sentence:
            continue
        if sentence.endswith(("?", "!", ":")):
            continue
        if _ENUMERATED_RE.match(sentence):
            continue
        normalized = sentence.rstrip('"”’)]}')
        if normalized.endswith(_POLITE_ENDINGS):
            violations.append(
                LintViolation(
                    rule="non_da_ending",
                    span=sentence,
                    suggestion="문장을 '-다' 종결형으로 맞추세요.",
                )
            )
            continue
        if not normalized.endswith(("다", "다.")):
            violations.append(
                LintViolation(
                    rule="non_da_ending",
                    span=sentence,
                    suggestion="문장을 '-다' 종결형으로 맞추세요.",
                )
            )
    return violations


def _lint_glossary(body: str) -> list[LintViolation]:
    violations: list[LintViolation] = []
    for canonical, variant, suggestion in _GLOSSARY_PAIRS:
        if canonical in body and variant in body:
            violations.append(
                LintViolation(
                    rule="glossary_inconsistency",
                    span=f"{canonical} / {variant}",
                    suggestion=suggestion,
                )
            )
    return violations


@dataclass(frozen=True)
class ForbiddenExpressionViolation:
    expression: str
    position: int
    context: str


@dataclass(frozen=True)
class ForbiddenExpressionsReport:
    violations: tuple[ForbiddenExpressionViolation, ...]
    ok: bool


def check_forbidden_expressions(
    text: str,
    domain: KIMMDomainPack = KIMM_DOMAIN,
) -> ForbiddenExpressionsReport:
    """Check for KIMM-forbidden expressions in proposal text."""
    violations: list[ForbiddenExpressionViolation] = []
    for expr in domain.forbidden_expressions:
        pos = 0
        while True:
            idx = text.find(expr, pos)
            if idx == -1:
                break
            context_start = max(0, idx - 20)
            context_end = min(len(text), idx + len(expr) + 20)
            violations.append(
                ForbiddenExpressionViolation(
                    expression=expr,
                    position=idx,
                    context=text[context_start:context_end],
                )
            )
            pos = idx + 1
    ordered = tuple(sorted(violations, key=lambda v: (v.position, v.expression)))
    return ForbiddenExpressionsReport(violations=ordered, ok=not violations)
