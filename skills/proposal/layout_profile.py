from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

PAGE_WIDTH: Final = 59_528
PAGE_HEIGHT: Final = 84_186
BODY_WIDTH: Final = 42_520
BODY_HEIGHT: Final = 74_266
FONT_HEIGHT: Final = 1_100
LINE_SPACING: Final = 1.6
LINE_HEIGHT: Final = int(FONT_HEIGHT * LINE_SPACING)
FIGURE_CAPTION_HEIGHT: Final = LINE_HEIGHT
FIGURE_GAP_HEIGHT: Final = LINE_HEIGHT // 2
FIGURE_PLACEMENT_WIDTH_MM: Final = 120
DEFAULT_CALIBRATED_CPP: Final = 1_350
MIN_CALIBRATED_CPP: Final = 1_250
MAX_CALIBRATED_CPP: Final = 1_450
DEFAULT_LAYOUT_PROFILE_NAME: Final = "10-page"
LEGACY_LAYOUT_PROFILE_NAME: Final = "10-page"


@dataclass(frozen=True)
class LayoutProfile:
    name: str
    section_page_targets: Mapping[int, int]
    figure_targets: Mapping[int, int]
    prose_budgets: Mapping[int, int]
    max_public_artifact_chars: int
    figure_downscale_mm: int | None = None


@dataclass(frozen=True)
class SectionLayout:
    prose_chars: int
    figure_heights: tuple[int, ...] = ()
    table_heights: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        values = (self.prose_chars, *self.figure_heights, *self.table_heights)
        if any(value < 0 for value in values):
            raise ValueError("layout dimensions and prose_chars must be non-negative")


@dataclass(frozen=True)
class LayoutSpec:
    sections: tuple[SectionLayout, ...]
    fixed_template_pe: float = 0.0
    heading_pe: float = 0.0

    def __post_init__(self) -> None:
        if self.fixed_template_pe < 0 or self.heading_pe < 0:
            raise ValueError("page-equivalent constants must be non-negative")


def _profile(
    name: str,
    *,
    pages: dict[int, int],
    figures: dict[int, int],
    prose: dict[int, int],
    max_chars: int,
    downscale_mm: int | None = None,
) -> LayoutProfile:
    return LayoutProfile(
        name=name,
        section_page_targets=MappingProxyType(pages),
        figure_targets=MappingProxyType(figures),
        prose_budgets=MappingProxyType(prose),
        max_public_artifact_chars=max_chars,
        figure_downscale_mm=downscale_mm,
    )


LAYOUT_PROFILES: Final[Mapping[str, LayoutProfile]] = MappingProxyType(
    {
        # The competition guide asks for "10 페이지 내외 (A4 / 돋움 11pt / 줄간격
        # 160%)", so this is the default. Exactly one figure per section: a band
        # must carry exactly one figure, a section with no band renders no prose
        # at all, and every extra figure forces a page break that costs a page.
        "10-page": _profile(
            "10-page",
            pages={0: 1, 1: 2, 2: 2, 3: 3, 4: 2},
            figures={0: 1, 1: 1, 2: 1, 3: 2, 4: 1},
            # pages x DEFAULT_CALIBRATED_CPP, less the share each section's own
            # figure and headings take of its pages (~0.33). The old numbers gave
            # sections 0 and 4 half the characters per page that 1 and 3 got,
            # which is drift: refine enforces these budgets, so a section sized
            # correctly for its page target failed the char-budget invariant.
            prose={0: 900, 1: 1_800, 2: 1_800, 3: 2_700, 4: 1_800},
            max_chars=15_000,
        ),
        "30-page": _profile(
            "30-page",
            pages={0: 2, 1: 8, 2: 4, 3: 12, 4: 4},
            figures={0: 1, 1: 4, 2: 2, 3: 6, 4: 2},
            prose={0: 1_350, 1: 6_000, 2: 2_800, 3: 9_000, 4: 2_800},
            max_chars=24_000,
            downscale_mm=FIGURE_PLACEMENT_WIDTH_MM,
        ),
    }
)


def get_layout_profile(name: str | None = None) -> LayoutProfile:
    selected = DEFAULT_LAYOUT_PROFILE_NAME if name is None else name
    try:
        return LAYOUT_PROFILES[selected]
    except KeyError as exc:
        valid = ", ".join(sorted(LAYOUT_PROFILES))
        raise ValueError(f"unknown layout profile {selected!r}; valid profiles: {valid}") from exc


def estimate_pages(
    spec: LayoutSpec,
    *,
    calibrated_cpp: float = DEFAULT_CALIBRATED_CPP,
) -> float:
    """Estimate page equivalents without reading or rendering an artifact."""
    if not MIN_CALIBRATED_CPP <= calibrated_cpp <= MAX_CALIBRATED_CPP:
        raise ValueError(
            f"calibrated_cpp must be between {MIN_CALIBRATED_CPP} and {MAX_CALIBRATED_CPP}"
        )

    prose_pe = sum(section.prose_chars for section in spec.sections) / calibrated_cpp
    figure_pe = sum(
        height + FIGURE_CAPTION_HEIGHT + FIGURE_GAP_HEIGHT
        for section in spec.sections
        for height in section.figure_heights
    ) / BODY_HEIGHT
    table_pe = sum(
        height for section in spec.sections for height in section.table_heights
    ) / BODY_HEIGHT
    return spec.fixed_template_pe + prose_pe + figure_pe + table_pe + spec.heading_pe


__all__ = [
    "BODY_HEIGHT",
    "BODY_WIDTH",
    "DEFAULT_CALIBRATED_CPP",
    "DEFAULT_LAYOUT_PROFILE_NAME",
    "FONT_HEIGHT",
    "FIGURE_PLACEMENT_WIDTH_MM",
    "LAYOUT_PROFILES",
    "LEGACY_LAYOUT_PROFILE_NAME",
    "LINE_HEIGHT",
    "LayoutProfile",
    "LayoutSpec",
    "MAX_CALIBRATED_CPP",
    "MIN_CALIBRATED_CPP",
    "PAGE_HEIGHT",
    "PAGE_WIDTH",
    "SectionLayout",
    "estimate_pages",
    "get_layout_profile",
]
