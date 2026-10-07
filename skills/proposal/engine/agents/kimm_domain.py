from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ─── Section Requirements ──────────────────────────────────────────────────────
# Maps rule.md mandatory section IDs to required sub-elements.
# Source: resource/rule.md "HWPX 결과물 가이드" (필수 5개 섹션):
#   0. 연구 요약문
#   1. 연구 배경 및 필요성
#   2. 연구 목표
#   3. 연구 내용 및 수행 방법
#   4. 기대효과 및 활용 방안
# Sub-elements follow common national R&D proposal practice
# (협력체계, 연차별 계획, TRL, 예산).

SECTION_REQUIREMENTS: Final[dict[str, list[str]]] = {
    "0": ["연구의 필요성 요약", "목표 요약", "기대효과 요약"],
    "1": ["연구 배경", "필요성", "기존 연구/기술 현황", "선행 연구와의 차별성"],
    "2": ["정량적 목표", "KPI", "TRL 목표", "연구 범위"],
    "3": [
        "세부 기술 내용",
        "연차별 계획",
        "협력체계",
        "컨소시엄 역할 분해",
        "연구 방법론",
    ],
    "4": ["기대 효과", "활용 방안", "성과 지표", "파급 효과"],
}

# Human-readable section titles (rule.md canonical names).
SECTION_TITLES: Final[dict[str, str]] = {
    "0": "연구 요약문",
    "1": "연구 배경 및 필요성",
    "2": "연구 목표",
    "3": "연구 내용 및 수행 방법",
    "4": "기대효과 및 활용 방안",
}

# ─── Style Rules ───────────────────────────────────────────────────────────────
# This project enforces 보고서체 (declarative "-다" endings), NOT polite
# "습니다" form. See agents/style_lint.py `_lint_da_endings`, which flags polite
# endings as violations and requires sentences to end in "-다". ALLOWED_ENDINGS
# below is aligned to that real enforcement so downstream style gating (task 15)
# stays consistent with style_lint.

ALLOWED_ENDINGS: Final[tuple[str, ...]] = (
    "이다",
    "한다",
    "된다",
    "있다",
    "없다",
    "였다",
    "했다",
    "된다",
    "한다.",
    "이다.",
    "다",  # generic declarative "-다" terminal
)

# Polite endings rejected by style_lint (kept here for explicit cross-reference).
FORBIDDEN_ENDINGS: Final[tuple[str, ...]] = (
    "습니다",
    "입니다",
    "합니다",
    "됩니다",
    "있습니다",
    "없습니다",
)

# Forbidden expressions in KIMM proposals (semantic, not terminal-form).
FORBIDDEN_EXPRESSIONS: Final[tuple[str, ...]] = (
    "것 같다",
    "것 같습니다",  # 추측 표현
    "아마도",
    "어쩌면",  # 불확실 표현
    "최고",
    "최대한",
    "무한",  # 과장 표현 (수치 없이)
    "etc",
    "etc.",  # 영문 약어
    "할 예정",  # 미래 불확실 (연구계획서에 부적합)
)

# ─── Korean proposal layout conventions ───────────────────────────────────────
# 정부·공공 R&D 계획서의 개조식 계층 기호 순서는 □ → ○ → - → · 다. The renderer
# normalises authored markers to these by indent depth, so the list lives here
# and both band_styles and the style linter read it instead of keeping copies.
BULLET_GLYPHS: Final[tuple[str, ...]] = ("□", "○", "-", "·")

# Figure captions are labels, not sentences. Korean declarative sentences end in
# -다, so a caption that does is narrative prose in a label's place.
CAPTION_SENTENCE_SUFFIX: Final = "다"

# Sections that must contain quantitative indicators (numbers/KPIs).
QUANTITATIVE_REQUIRED_SECTIONS: Final[tuple[str, ...]] = ("2", "3", "4")

# 외래어 표기법(국립국어원)과 어긋나는 흔한 표기 → 표준 표기. 한 문서에 두 표기가
# 섞이면 평가가 「용어 혼용」으로 감점한다(예: 버켓/버킷).
STANDARD_SPELLINGS: Final[tuple[tuple[str, str], ...]] = (
    ("버켓", "버킷"),
    ("데이타", "데이터"),
    ("메세지", "메시지"),
    ("컨텐츠", "콘텐츠"),
    ("워크샵", "워크숍"),
    ("타겟", "타깃"),
    ("리더쉽", "리더십"),
)

# ─── KPI / TRL / Budget Schema ─────────────────────────────────────────────────


@dataclass(frozen=True)
class KPISchema:
    """Schema for a single KPI entry validation."""

    name: str  # e.g. "기술준비도(TRL)"
    unit: str  # e.g. "Level", "%", "건", "편"
    min_value: float = 0.0
    max_value: float = float("inf")


@dataclass(frozen=True)
class TRLRequirement:
    """TRL monotonicity requirement across project years."""

    start_min: int = 3
    end_min: int = 6
    must_increase: bool = True  # each year must be >= previous


@dataclass(frozen=True)
class BudgetSchema:
    """Budget validation schema (annual variance + indirect-cost bounds)."""

    max_annual_variance_pct: float = 0.30  # 연차별 최대 30% 편차
    min_indirect_rate: float = 0.0  # 간접비 최소 비율
    max_indirect_rate: float = 0.30  # 간접비 최대 30%


@dataclass(frozen=True)
class KIMMDomainPack:
    """KIMM proposal domain rules and schemas (single immutable config)."""

    section_requirements: dict[str, list[str]]
    section_titles: dict[str, str]
    allowed_endings: tuple[str, ...]
    forbidden_endings: tuple[str, ...]
    forbidden_expressions: tuple[str, ...]
    quantitative_required_sections: tuple[str, ...]
    bullet_glyphs: tuple[str, ...]
    caption_sentence_suffix: str
    kpi_schema: tuple[KPISchema, ...]
    trl_requirement: TRLRequirement
    budget_schema: BudgetSchema


KIMM_DOMAIN: Final[KIMMDomainPack] = KIMMDomainPack(
    section_requirements=SECTION_REQUIREMENTS,
    section_titles=SECTION_TITLES,
    allowed_endings=ALLOWED_ENDINGS,
    forbidden_endings=FORBIDDEN_ENDINGS,
    forbidden_expressions=FORBIDDEN_EXPRESSIONS,
    quantitative_required_sections=QUANTITATIVE_REQUIRED_SECTIONS,
    bullet_glyphs=BULLET_GLYPHS,
    caption_sentence_suffix=CAPTION_SENTENCE_SUFFIX,
    kpi_schema=(
        KPISchema("기술준비도(TRL)", "Level", 1.0, 9.0),
        KPISchema("논문 게재", "편", 0.0),
        KPISchema("특허 출원", "건", 0.0),
        KPISchema("기술이전", "건", 0.0),
    ),
    trl_requirement=TRLRequirement(start_min=3, end_min=6, must_increase=True),
    budget_schema=BudgetSchema(),
)

__all__ = [
    "ALLOWED_ENDINGS",
    "BULLET_GLYPHS",
    "CAPTION_SENTENCE_SUFFIX",
    "BudgetSchema",
    "FORBIDDEN_ENDINGS",
    "FORBIDDEN_EXPRESSIONS",
    "KIMM_DOMAIN",
    "KIMMDomainPack",
    "KPISchema",
    "QUANTITATIVE_REQUIRED_SECTIONS",
    "SECTION_REQUIREMENTS",
    "SECTION_TITLES",
    "STANDARD_SPELLINGS",
    "TRLRequirement",
]
