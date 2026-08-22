"""Related-note rendering and private traceability for topics and weekly trends."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, cast

from . import topics_knowledge


class Item(Protocol):
    id: str
    store: str
    ref: str
    doc_date: str | None
    sensitivity: str | None
    content: str


class Pack(Protocol):
    verdict: str
    items: tuple[Item, ...]
    layers: dict[str, str]


class CitationReport(Protocol):
    text: str


class Renderer(Protocol):
    def render_citations(self, pack: Pack, style: str) -> str: ...
    def render_verdict(self, pack: Pack) -> str: ...
    def validate_citations(self, text: str, pack: Pack) -> CitationReport: ...


def _renderer() -> Renderer:
    module = topics_knowledge.module("automation.knowledge.render")
    return cast(Renderer, cast(object, module))


def prompt_block(evidence_pack: object) -> str:
    pack = cast(Pack, evidence_pack)
    if pack.verdict != "hit":
        try:
            return _renderer().render_citations(pack, "sources")
        except ImportError:
            return "EVIDENCE: unavailable — 근거 수집 불가"
    records = [
        f"[{item.id}] store={item.store}; ref={item.ref}; "
        f"date={item.doc_date or '날짜 미상'}; content={item.content}"
        for item in pack.items
    ]
    return "EVIDENCE:\n" + "\n".join(records)


def render_section(evidence_pack: object) -> str:
    pack = cast(Pack, evidence_pack)
    try:
        renderer = _renderer()
        body = "\n\n".join(part for part in (
            renderer.render_verdict(pack), renderer.render_citations(pack, "sources")
        ) if part)
    except ImportError:
        body = "근거 수집 불가 — 지식 파사드를 불러오지 못했지만 생성을 계속함"
    return f"## 내 관련 노트\n\n{body}"


def validate(text: str, evidence_pack: object) -> str:
    try:
        return _renderer().validate_citations(text, cast(Pack, evidence_pack)).text
    except ImportError:
        return text


def is_sensitive(evidence_pack: object) -> bool:
    pack = cast(Pack, evidence_pack)
    return any(
        item.sensitivity == "patent-sensitive"
        or "[[PATENT-SENSITIVE-RECALL]]" in item.content
        for item in pack.items
    )


def write_sidecar(path: Path, evidence_pack: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    target = path.with_suffix(".evidence.json")
    target.write_text(
        json.dumps(asdict(cast(Any, evidence_pack)), ensure_ascii=False, indent=2, default=sorted) + "\n",
        encoding="utf-8",
    )
    target.chmod(0o600)
    return target


def preview(evidence_pack: object, *, as_json: bool) -> str:
    pack = cast(Pack, evidence_pack)
    if as_json:
        return json.dumps(
            {"evidence_count": len(pack.items), "layers": pack.layers},
            ensure_ascii=False, sort_keys=True,
        )
    return render_section(pack)
