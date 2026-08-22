"""Thin topics adapter for the read-only knowledge facade."""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from automation.knowledge.pack import EvidencePack, KnowledgeQuery


class _QueryFactory(Protocol):
    def __call__(self, text: str, *, purpose: str, limit: int, caller: str) -> KnowledgeQuery: ...


class _Facade(Protocol):
    KnowledgeQuery: _QueryFactory
    collect_evidence: Callable[[KnowledgeQuery], EvidencePack]


@dataclass(frozen=True, slots=True)
class _UnavailableQuery:
    text: str
    purpose: str = "synthesize"
    sources: frozenset[str] = frozenset({"rag", "wiki", "twin"})
    tags: frozenset[str] = frozenset()
    limit: int = 8
    caller: str = "topics"


@dataclass(frozen=True, slots=True)
class _UnavailablePack:
    version: str
    query: _UnavailableQuery
    verdict: str
    items: tuple[()]
    layers: dict[str, str]
    notes: tuple[str, ...]


def _repo_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    candidates = (*here.parents[2:6], Path("/srv/autophagy-agent-current"), Path("/srv/autophagy-agents"))
    for candidate in candidates:
        if (candidate / "automation" / "knowledge").is_dir():
            return candidate
    return Path("/srv/autophagy-agent-current")


def module(name: str) -> ModuleType:
    """Load one shared knowledge module from the configured repository checkout."""
    root = _repo_root()
    sys.path.insert(0, str(root))
    return importlib.import_module(name)


def _without_self_reference(pack: EvidencePack) -> EvidencePack:
    kept = tuple(
        item for item in pack.items
        if not (
            item.source_type == "note"
            and item.ref.removeprefix("note:").startswith("research-trends/")
        )
    )
    if len(kept) == len(pack.items):
        return pack
    renumbered = tuple(replace(item, id=f"E{index}") for index, item in enumerate(kept, 1))
    verdict = pack.verdict if renumbered else "no_evidence"
    return replace(
        pack, verdict=verdict, items=renumbered,
        notes=(*pack.notes, f"research-trends 자기참조 {len(pack.items) - len(kept)}건 제외"),
    )


def collect(topics: tuple[str, ...], *, limit: int = 8) -> EvidencePack | _UnavailablePack:
    """Collect related owner notes while excluding reingested weekly reports."""
    text = "\n".join(topic.strip() for topic in topics if topic.strip())
    try:
        facade = cast(_Facade, cast(object, module("automation.knowledge.facade")))
        query = facade.KnowledgeQuery(
            text, purpose="synthesize", limit=min(limit, 8), caller="topics"
        )
        return _without_self_reference(facade.collect_evidence(query))
    except Exception as error:
        # The weekly no-agent cron must survive local credentials and source outages;
        # importing facade implementation exception types here would break R3.
        query = _UnavailableQuery(text, limit=min(limit, 8))
        layers = {"rag": "unavailable", "wiki": "unavailable", "twin": "unavailable"}
        return _UnavailablePack(
            "knowledge-v1", query, "unavailable", (), layers,
            (f"knowledge collection unavailable({error.__class__.__name__})",),
        )
