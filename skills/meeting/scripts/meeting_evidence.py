"""Knowledge prompt, citation integrity, and private traceability for meetings."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, cast

import meeting_knowledge
import meeting_llm


class Item(Protocol):
    id: str
    store: str
    ref: str
    doc_date: str | None
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
    module = meeting_knowledge.module("automation.knowledge.render")
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


def finalize(
    extraction: meeting_llm.Extraction, evidence_pack: object
) -> tuple[meeting_llm.Extraction, str]:
    pack = cast(Pack, evidence_pack)
    try:
        renderer = _renderer()
        cleaned = meeting_llm.map_extraction(
            extraction, lambda text: renderer.validate_citations(text, pack).text
        )
        footer = "\n\n".join(
            part for part in (
                renderer.render_verdict(pack), renderer.render_citations(pack, "sources")
            ) if part
        )
        return cleaned, footer
    except ImportError:
        return extraction, "근거 수집 불가 — 지식 파사드를 불러오지 못했지만 생성을 계속함"


def write_sidecar(note_path: Path, evidence_pack: object) -> Path:
    path = note_path.with_suffix(".evidence.json")
    payload = asdict(cast(Any, evidence_pack))
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=sorted) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def command(args: object, evidence_pack: object | None = None) -> int:
    pack = evidence_pack or meeting_knowledge.collect(
        str(getattr(args, "title")), str(getattr(args, "attendees")),
        str(getattr(args, "topics")), limit=int(getattr(args, "limit")),
    )
    typed = cast(Pack, pack)
    if bool(getattr(args, "json")):
        print(json.dumps(
            {"evidence_count": len(typed.items), "layers": typed.layers},
            ensure_ascii=False, sort_keys=True,
        ))
    else:
        print(_renderer().render_citations(typed, "sources"))
    return 0
