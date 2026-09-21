from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).absolute().parents[2]))
    __package__ = "proposal.scripts"

from ..layout_profile import LAYOUT_PROFILES  # noqa: E402


@dataclass(frozen=True, slots=True)
class FigureSpec:
    figure_id: str
    section_id: str
    source_claim_ids: tuple[str, ...]
    prompt: str
    caption: str
    png_sha256: str
    band_index: int


@dataclass(frozen=True, slots=True)
class TableSpec:
    table_id: str
    section_id: str
    kind: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in {"prior-research", "tech-gap", "kpi", "gantt"}:
            raise ValueError(f"invalid table kind: {self.kind}")


@dataclass(frozen=True, slots=True)
class SectionSpec:
    section_id: int
    target_pages: int
    prose_char_budget: int
    figure_slots: int

    def __post_init__(self) -> None:
        if not 0 <= self.section_id <= 4:
            raise ValueError(f"invalid section id: {self.section_id}")


@dataclass(frozen=True, slots=True)
class LayoutProfile:
    name: str
    sections: tuple[SectionSpec, ...]


# Keep the skill's section-oriented API; the public contract owns profile values.
PROFILES: Final[dict[str, LayoutProfile]] = {
    name: LayoutProfile(
        name,
        tuple(
            SectionSpec(
                section_id=section_id,
                target_pages=profile.section_page_targets[section_id],
                prose_char_budget=profile.prose_budgets[section_id],
                figure_slots=profile.figure_targets[section_id],
            )
            for section_id in sorted(profile.section_page_targets)
        ),
    )
    for name, profile in LAYOUT_PROFILES.items()
}

FIG_TOKEN_RE = re.compile(r"\[\[FIG:([a-z0-9][a-z0-9-]*)\]\]")
_CAPTION_RE = re.compile(r"그림 (\d+)")


class UnknownFigureToken(ValueError):
    pass


def resolve_figure_tokens(text: str, figures: Sequence[FigureSpec]) -> tuple[str, dict[str, int]]:
    by_id = {figure.figure_id: figure for figure in figures}
    mapping: dict[str, int] = {}
    for match in FIG_TOKEN_RE.finditer(text):
        token_id = match.group(1)
        if token_id not in by_id:
            raise UnknownFigureToken(token_id)
        if token_id not in mapping:
            mapping[token_id] = len(mapping) + 1
    for figure in figures:
        if figure.figure_id not in mapping:
            mapping[figure.figure_id] = len(mapping) + 1
    return FIG_TOKEN_RE.sub(lambda m: f"그림 {mapping[m.group(1)]}", text), mapping


def caption_numbers(figures: Sequence[FigureSpec], mapping: dict[str, int]) -> set[int]:
    return {mapping[figure.figure_id] for figure in figures}


def referenced_numbers(resolved_text: str) -> set[int]:
    return {int(number) for number in _CAPTION_RE.findall(resolved_text)}


def check_caption_reference_consistency(
    resolved_text: str, mapping: dict[str, int]
) -> tuple[bool, str]:
    expected = set(mapping.values())
    actual = referenced_numbers(resolved_text)
    message = f"caption/reference mismatch: {actual} != {expected}"
    return (True, "") if actual == expected else (False, message)


def _dump(items: Sequence[Any]) -> str:
    return json.dumps(
        [dataclasses.asdict(item) for item in items],
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
    )


def figures_to_json(figs: Sequence[FigureSpec]) -> str:
    return _dump(figs)


def figures_from_json(value: str) -> tuple[FigureSpec, ...]:
    return tuple(
        FigureSpec(
            d["figure_id"], d["section_id"], tuple(d["source_claim_ids"]),
            d["prompt"], d["caption"], d["png_sha256"], d["band_index"]
        )
        for d in json.loads(value)
    )


def tables_to_json(tables: Sequence[TableSpec]) -> str:
    return _dump(tables)


def tables_from_json(value: str) -> tuple[TableSpec, ...]:
    return tuple(
        TableSpec(
            d["table_id"], d["section_id"], d["kind"], tuple(d["header"]),
            tuple(tuple(row) for row in d["rows"]), tuple(d["source_ids"])
        )
        for d in json.loads(value)
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--figures", required=True)
    resolve.add_argument("--text", required=True)
    args = parser.parse_args(argv)
    if args.command == "resolve":
        try:
            figures = figures_from_json(Path(args.figures).read_text(encoding="utf-8"))
            text, _ = resolve_figure_tokens(Path(args.text).read_text(encoding="utf-8"), figures)
        except UnknownFigureToken as exc:
            print(f"UNKNOWN-FIGURE-TOKEN: {exc}", file=sys.stderr)
            return 3
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"INVALID-INPUT: {exc}", file=sys.stderr)
            return 2
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
