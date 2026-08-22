"""Knowledge-backed consultation and draft traceability for the wiki CLI."""

from __future__ import annotations

import json
import re
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

wiki_knowledge = import_module(".wiki_knowledge", __package__) if __package__ else import_module("wiki_knowledge")
wiki_store = import_module(".wiki_store", __package__) if __package__ else import_module("wiki_store")

_TOKEN = re.compile(r"[0-9A-Za-z가-힣._-]+")


class Item(Protocol):
    id: str
    store: str
    source_type: str
    ref: str
    title: str
    doc_date: str | None
    date_basis: str
    score: float | None
    grounded: bool | None
    authority: str | None
    expired: bool | None
    sensitivity: str | None
    content: str
    sha256: str


class Query(Protocol):
    text: str
    purpose: str
    sources: frozenset[str]
    tags: frozenset[str]
    limit: int
    caller: str


class Pack(Protocol):
    version: str
    query: Query
    verdict: str
    items: tuple[Item, ...]
    layers: dict[str, str]
    notes: tuple[str, ...]


class CitationReport(Protocol):
    text: str
    stripped_ids: tuple[str, ...]


class Renderer(Protocol):
    def render_citations(self, pack: Pack, style: str) -> str: ...
    def render_verdict(self, pack: Pack) -> str: ...
    def validate_citations(self, text: str, pack: Pack) -> CitationReport: ...


def _renderer() -> Renderer:
    module = wiki_knowledge.module("automation.knowledge.render")
    return cast(Renderer, cast(object, module))


def actual_tags(root: Path, text: str) -> frozenset[str]:
    """Intersect deterministic query tokens with the wiki's real tag vocabulary."""
    vocabulary: dict[str, str] = {}
    for note in wiki_store.iter_notes(root):
        for tag in note.meta["tags"]:
            vocabulary.setdefault(str(tag).casefold(), str(tag))
    tokens = {token.casefold() for token in _TOKEN.findall(text)}
    return frozenset(vocabulary[token] for token in sorted(tokens.intersection(vocabulary)))


def collect(root: Path, text: str, *, limit: int) -> Pack:
    tags = actual_tags(root, text)
    return cast(Pack, cast(object, wiki_knowledge.collect(text, tags, limit=limit)))


def collect_draft(title: str, tags: list[str], body: str) -> Pack:
    text = "\n".join((title.strip(), body.strip()))
    return cast(Pack, cast(object, wiki_knowledge.collect(text, frozenset(tags))))


def prepare_body(body: str, evidence_pack: object) -> str:
    """Validate pack-bound citations and append only facade-rendered sources."""
    pack = cast(Pack, evidence_pack)
    renderer = _renderer()
    report = renderer.validate_citations(body, pack)
    verdict = renderer.render_verdict(pack)
    sources = renderer.render_citations(pack, "sources")
    parts = [part for part in (verdict, report.text, f"## Sources\n\n{sources}") if part]
    return "\n\n".join(parts)


def _label(item: Item, conflict: bool) -> str:
    if item.store == "wiki":
        if conflict or item.expired or item.authority not in {"strict", "default"}:
            return "[불확실·충돌]"
        return "[위키 규칙]"
    return "[RAG 선례]"


def render_consult(evidence_pack: object) -> str:
    """Preserve facade citation bytes while adding deterministic layer labels."""
    pack = cast(Pack, evidence_pack)
    rendered = _renderer().render_citations(pack, "consult")
    if pack.verdict != "hit":
        return rendered
    lines = rendered.splitlines()
    conflict = any(state == "conflict" for state in pack.layers.values())
    labeled = [f"{_label(item, conflict)} {line}" for item, line in zip(pack.items, lines, strict=True)]
    if conflict:
        labeled.append("[불확실·충돌] 팩 계층 상태에 conflict가 있어 소유자 판단이 필요함")
    return "\n".join(labeled)


def pack_dict(pack: Pack) -> dict[str, object]:
    query = pack.query
    return {
        "version": pack.version,
        "query": {
            "text": query.text, "purpose": query.purpose,
            "sources": sorted(query.sources), "tags": sorted(query.tags),
            "limit": query.limit, "caller": query.caller,
        },
        "verdict": pack.verdict,
        "items": [
            {
                "id": item.id, "store": item.store, "source_type": item.source_type,
                "ref": item.ref, "title": item.title, "doc_date": item.doc_date,
                "date_basis": item.date_basis, "score": item.score,
                "grounded": item.grounded, "authority": item.authority,
                "expired": item.expired, "sensitivity": item.sensitivity,
                "content": item.content, "sha256": item.sha256,
            }
            for item in pack.items
        ],
        "layers": pack.layers,
        "notes": list(pack.notes),
    }


def write_sidecar(gate_root: Path, draft_id: str, evidence_pack: object) -> Path:
    pack = cast(Pack, evidence_pack)
    directory = gate_root / "evidence"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / f"{draft_id}.evidence.json"
    path.write_text(json.dumps(pack_dict(pack), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def prepare_draft(
    title: str, tags: list[str], body: str, enabled: bool, evidence_pack: object | None,
) -> tuple[str, object | None]:
    pack = evidence_pack
    if enabled and pack is None:
        pack = collect_draft(title, tags, body)
    return (prepare_body(body, pack), pack) if pack is not None else (body, None)


def command_consult(root: Path, args: object, evidence_pack: object | None = None) -> int:
    text = str(getattr(args, "text"))
    limit = int(getattr(args, "limit"))
    pack = collect(root, text, limit=limit) if evidence_pack is None else cast(Pack, evidence_pack)
    if bool(getattr(args, "json")):
        print(json.dumps(
            {"evidence_count": len(pack.items), "layers": pack.layers},
            ensure_ascii=False, sort_keys=True,
        ))
    else:
        print(render_consult(pack))
    return 0
