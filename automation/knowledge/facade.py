"""Read-only knowledge facade: plan, fetch, gate, rank, and pack."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from automation.knowledge.pack import EvidenceItem, EvidencePack, KnowledgeQuery
from automation.knowledge.plan import analyze_query
from automation.knowledge.rank import rank_and_deduplicate
from automation.knowledge.testing import FakeSources, load_fake_pack

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _from_fake(query: KnowledgeQuery, fake: FakeSources) -> tuple[list[EvidenceItem], dict[str, str], list[str]]:
    items: list[EvidenceItem] = []
    layers = {"rag": "skipped", "wiki": "skipped", "twin": "skipped"}
    notes = list(fake.notes)
    specs = (
        ("rag", fake.rag_items, fake.rag_status, fake.rag_error),
        ("wiki", fake.wiki_items, fake.wiki_status, fake.wiki_error),
        ("twin", fake.twin_items, fake.twin_status, fake.twin_error),
    )
    for name, source_items, status, error in specs:
        if name not in query.sources:
            continue
        if error is not None:
            layers[name] = "unavailable"
            notes.append(f"{name} 계층 불가({error.__class__.__name__})")
        else:
            layers[name] = status
            items.extend(source_items)
    return items, layers, notes


def _from_live(query: KnowledgeQuery, now: datetime, env: Mapping[str, str]) -> tuple[list[EvidenceItem], dict[str, str], list[str]]:
    items: list[EvidenceItem] = []
    layers = {"rag": "skipped", "wiki": "skipped", "twin": "skipped"}
    notes: list[str] = []
    plan = analyze_query(query.text, query.purpose)
    if "rag" in query.sources:
        try:
            from automation.knowledge.adapters.rag import fetch_rag

            result = fetch_rag(query, plan, env)
            layers["rag"] = result.status
            items.extend(result.items)
            notes.extend(result.notes)
        except ImportError as error:
            layers["rag"] = "unavailable"
            notes.append(f"rag 계층 불가({error.__class__.__name__})")
    if query.sources.intersection({"wiki", "twin"}):
        try:
            from automation.knowledge.adapters.wiki import fetch_wiki

            result = fetch_wiki(query, now, env)
            if "wiki" in query.sources:
                layers["wiki"] = result.wiki_status
                items.extend(result.wiki_items)
            if "twin" in query.sources:
                layers["twin"] = result.twin_status
                items.extend(result.twin_items)
            notes.extend(result.notes)
        except (ImportError, OSError) as error:
            for name in query.sources.intersection({"wiki", "twin"}):
                layers[name] = "unavailable"
            notes.append(f"wiki 계층 불가({error.__class__.__name__})")
    return items, layers, notes


def collect_evidence(query: KnowledgeQuery, *, clock: Clock = _now, env: Mapping[str, str] | FakeSources = os.environ) -> EvidencePack:
    """Collect one immutable pack without writes, retries, or threshold changes."""
    if isinstance(env, FakeSources):
        items, layers, notes = _from_fake(query, env)
    else:
        fake_pack = load_fake_pack(env)
        if fake_pack is not None:
            return fake_pack
        items, layers, notes = _from_live(query, clock(), env)
    ranked = rank_and_deduplicate(items, limit=query.limit)
    requested = [layers[source] for source in query.sources]
    if ranked:
        verdict = "hit"
    elif any(status == "unavailable" for status in requested):
        verdict = "unavailable"
    else:
        verdict = "no_evidence"
    return EvidencePack("knowledge-v1", query, verdict, ranked, layers, tuple(dict.fromkeys(notes)))
