"""Thin mail adapter for the read-only knowledge facade."""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
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
    caller: str = "mail"


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


def unavailable(
    counterparty: str, subject: str, material: str, *,
    limit: int = 8, reason: str = "entity preflight import unavailable",
) -> _UnavailablePack:
    """Return the deterministic pack used when evidence preflight cannot load."""
    text = "\n".join((counterparty.strip(), subject.strip(), material.strip()))
    query = _UnavailableQuery(text, limit=min(limit, 8))
    layers = {"rag": "unavailable", "wiki": "unavailable", "twin": "unavailable"}
    return _UnavailablePack(
        "knowledge-v1", query, "unavailable", (), layers,
        (reason,),
    )


def collect(
    counterparty: str, subject: str, material: str, *, limit: int = 8
) -> EvidencePack | _UnavailablePack:
    """Build one bounded counterparty/topic query and collect exactly one facade pack."""
    text = "\n".join((counterparty.strip(), subject.strip(), material.strip()))
    try:
        facade = cast(_Facade, cast(object, module("automation.knowledge.facade")))
        query = facade.KnowledgeQuery(
            text, purpose="synthesize", limit=min(limit, 8), caller="mail"
        )
        return facade.collect_evidence(query)
    except ImportError:
        return unavailable(
            counterparty, subject, material, limit=limit,
            reason="knowledge facade import unavailable",
        )
