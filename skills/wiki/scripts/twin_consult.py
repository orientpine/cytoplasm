#!/usr/bin/env python3
"""Decision-twin read-only consultation helper.

``consult`` returns ``verdict=none`` when no active twin rules match, ``conflict``
when the top two matching rules cover the same kind and at least one shared tag
but disagree in the ranked head (status/provenance/effective-authority), and
``ok`` otherwise. It only reads notes through ``wiki_store.iter_notes``.
"""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import wiki_store
from wiki_store import AUTHORITY_VALUES, KIND_VALUES, PROVENANCE_VALUES, STATUS_VALUES, _DATE_RE

JUDGMENT_KINDS: Final = tuple(kind for kind in KIND_VALUES if kind != "note")


def _descending_rank(values: Sequence[str]) -> dict[str, int]:
    return {value: len(values) - index for index, value in enumerate(values)}


STATUS_RANK: Final = _descending_rank(STATUS_VALUES)
PROVENANCE_RANK: Final = _descending_rank(PROVENANCE_VALUES)
AUTHORITY_RANK: Final = _descending_rank(AUTHORITY_VALUES)


@dataclass(frozen=True, slots=True)
class ConsultQuery:
    tags: frozenset[str]
    kinds: frozenset[str]
    now: datetime


@dataclass(frozen=True, slots=True)
class RankedRule:
    slug: str
    tags: frozenset[str]
    kind: str
    authority: str
    authority_declared: str
    provenance: str
    status: str
    updated: str
    updated_dt: datetime
    expired: bool

    def output(self) -> dict:
        return {
            "slug": self.slug,
            "kind": self.kind,
            "authority": self.authority,
            "authority_declared": self.authority_declared,
            "provenance": self.provenance,
            "status": self.status,
            "updated": self.updated,
            "expired": self.expired,
        }


def _split_csv(raw: str | None) -> list[str]:
    if raw is None:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _updated(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _is_expired(review_after: str | None, now: datetime) -> bool:
    if review_after is None or not _DATE_RE.match(review_after):
        return False
    review_date = datetime.strptime(review_after, "%Y-%m-%d").date()
    return review_date < now.date()


def _demote(authority: str) -> str:
    match authority:
        case "strict":
            return "default"
        case "default":
            return "advisory"
        case "advisory":
            return "advisory"
        case _:
            return authority


def _candidate(note: wiki_store.Note, query: ConsultQuery) -> RankedRule | None:
    meta = note.meta
    kind = meta.get("kind")
    status = meta.get("status", "active")
    note_tags = frozenset(meta.get("tags", []))
    if kind not in JUDGMENT_KINDS:
        return None
    if status != "active":
        return None
    if query.kinds and kind not in query.kinds:
        return None
    if query.tags and not note_tags.intersection(query.tags):
        return None
    authority_declared = meta["authority"]
    review_after = meta.get("review_after")
    expired = _is_expired(review_after, query.now)
    authority = _demote(authority_declared) if expired else authority_declared
    updated = meta["updated"]
    return RankedRule(
        slug=note.slug,
        tags=note_tags,
        kind=kind,
        authority=authority,
        authority_declared=authority_declared,
        provenance=meta["provenance"],
        status=status,
        updated=updated,
        updated_dt=_updated(updated),
        expired=expired,
    )


def _rank_key(rule: RankedRule) -> tuple[int, int, int, float, str]:
    return (
        -STATUS_RANK[rule.status],
        -PROVENANCE_RANK[rule.provenance],
        -AUTHORITY_RANK[rule.authority],
        -rule.updated_dt.timestamp(),
        rule.slug,
    )


def _rank_head(rule: RankedRule) -> tuple[str, str, str]:
    return (rule.status, rule.provenance, rule.authority)


def _verdict(rules: list[RankedRule]) -> str:
    if not rules:
        return "none"
    if len(rules) < 2:
        return "ok"
    first, second = rules[0], rules[1]
    same_scope = first.kind == second.kind and bool(first.tags.intersection(second.tags))
    if same_scope and _rank_head(first) != _rank_head(second):
        return "conflict"
    return "ok"


def consult(root: Path, tags: list[str], kinds: list[str] | None, now: datetime) -> dict:
    """Rank active decision-twin rules from ``root`` without writes or network access."""
    query = ConsultQuery(tags=frozenset(tags), kinds=frozenset(kinds or []), now=now)
    rules = [
        rule
        for note in wiki_store.iter_notes(root)
        if (rule := _candidate(note, query)) is not None
    ]
    ranked = sorted(rules, key=_rank_key)
    return {"rules": [rule.output() for rule in ranked], "verdict": _verdict(ranked)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="twin_consult", description=__doc__)
    parser.add_argument("--root", default=os.environ.get("WIKI_ROOT", "~/wiki"))
    parser.add_argument("--tags", required=True, help="Comma-separated tag filter")
    parser.add_argument("--kinds", help="Comma-separated kind filter")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = consult(
        Path(args.root).expanduser(),
        _split_csv(args.tags),
        _split_csv(args.kinds) if args.kinds else None,
        datetime.now(timezone.utc),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
