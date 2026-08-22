"""Importlib adapter for the deployed wiki and decision-twin scripts."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Mapping

from automation.knowledge.core import tokenize
from automation.knowledge.pack import EvidenceItem, KnowledgeQuery
from automation.knowledge.rank import item_from_wiki
from automation.rag_ingest.sensitivity import SensitivityRulesError, classify, load_rules

_LIVE_SCRIPTS = "/srv/autophagy-skills/live/wiki/scripts"


@dataclass(frozen=True, slots=True)
class WikiFetch:
    wiki_items: tuple[EvidenceItem, ...]
    twin_items: tuple[EvidenceItem, ...]
    wiki_status: str
    twin_status: str
    notes: tuple[str, ...]


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _expired(meta: dict[str, object], now: datetime) -> bool:
    review_after = meta.get("review_after")
    return isinstance(review_after, str) and review_after < now.date().isoformat()


def _note_payload(note: object, now: datetime) -> dict[str, object]:
    meta = dict(getattr(note, "meta"))
    return {
        "slug": str(getattr(note, "slug")),
        "meta": meta,
        "body": str(getattr(note, "body")),
        "expired": _expired(meta, now),
    }


def fetch_wiki(query: KnowledgeQuery, now: datetime, env: Mapping[str, str]) -> WikiFetch:
    scripts = Path(env.get("WIKI_SCRIPTS", _LIVE_SCRIPTS)).expanduser()
    if not scripts.is_dir():
        raise ImportError("wiki scripts unavailable")
    store = _load(scripts, "wiki_store")
    consult_module = _load(scripts, "twin_consult")
    root = Path(env.get("WIKI_ROOT", "~/wiki")).expanduser()
    rules_path = Path(env.get("KNOWLEDGE_SENSITIVITY_RULES", "~/.hermes/rag-ingest/sensitivity-rules.yaml")).expanduser()
    try:
        rules = load_rules(rules_path)
    except (OSError, SensitivityRulesError):
        rules = None

    tokens = frozenset(tokenize(query.text))
    wiki_items: list[EvidenceItem] = []
    sensitive_excluded = unclassified = 0
    notes_by_slug: dict[str, object] = {}
    for note in store.iter_notes(root):
        notes_by_slug[str(note.slug)] = note
        meta = dict(note.meta)
        if meta.get("status", "active") != "active":
            continue
        subject = " ".join((*meta.get("entity", []), *meta.get("relations", [])))
        haystack = f"{meta.get('title', '')} {' '.join(meta.get('tags', []))} {subject} {note.body}"
        if tokens and not any(token.casefold() in haystack.casefold() for token in tokens):
            continue
        if rules is None:
            unclassified += 1
            continue
        tags = classify(haystack, rules)
        if "patent-sensitive" in tags:
            sensitive_excluded += 1
            continue
        wiki_items.append(item_from_wiki(_note_payload(note, now)))

    twin_items: list[EvidenceItem] = []
    twin_status = "none"
    if "twin" in query.sources:
        tags = list(query.tags or tokens)
        result = consult_module.consult(root, tags, None, now)
        twin_status = str(result.get("verdict", "none"))
        for rule in result.get("rules", []):
            note = notes_by_slug.get(str(rule.get("slug", "")))
            if note is None or rules is None:
                unclassified += 1
                continue
            payload = _note_payload(note, now)
            text = f"{payload['meta']} {payload['body']}"
            if "patent-sensitive" in classify(text, rules):
                sensitive_excluded += 1
                continue
            payload.update(authority=rule.get("authority"), expired=rule.get("expired"))
            twin_items.append(item_from_wiki(payload, twin=True))
    notes: list[str] = []
    if sensitive_excluded:
        notes.append(f"wiki/twin {sensitive_excluded}건 민감 제외")
    if unclassified:
        notes.append(f"wiki/twin {unclassified}건 분류 불가 제외")
    return WikiFetch(tuple(wiki_items), tuple(twin_items), "hit" if wiki_items else "none", twin_status, tuple(notes))
