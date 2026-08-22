"""Private evidence material and owner-only traceability for mail drafts."""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, cast

mail_knowledge = importlib.import_module("mail_knowledge")

_CITATION = re.compile(r"\[E\d+\]")


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
    module = mail_knowledge.module("automation.knowledge.render")
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


def owner_notice(evidence_pack: object) -> str:
    pack = cast(Pack, evidence_pack)
    try:
        renderer = _renderer()
        body = "\n".join(part for part in (
            renderer.render_verdict(pack), renderer.render_citations(pack, "sources")
        ) if part)
    except ImportError:
        body = "근거 수집 불가 — 지식 파사드를 불러오지 못했지만 초안 생성을 계속함"
    return f"\n\n[소유자 전용 근거]\n{body}"


def sanitize_draft_body(body: str, evidence_pack: object) -> str:
    """Validate generated citations, then remove all source ids from recipient text."""
    try:
        validated = _renderer().validate_citations(body, cast(Pack, evidence_pack)).text
    except ImportError:
        validated = body
    return re.sub(r" {2,}", " ", _CITATION.sub("", validated)).strip()


def evidence_text(evidence_pack: object) -> str:
    return "\n".join(item.content for item in cast(Pack, evidence_pack).items)


def write_sidecar(gate_root: Path, draft_id: str, evidence_pack: object) -> Path:
    directory = gate_root / "evidence"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / f"{draft_id}.evidence.json"
    path.write_text(
        json.dumps(asdict(cast(Any, evidence_pack)), ensure_ascii=False, indent=2, default=sorted) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def preview(evidence_pack: object, *, as_json: bool) -> str:
    pack = cast(Pack, evidence_pack)
    if as_json:
        return json.dumps(
            {"evidence_count": len(pack.items), "layers": pack.layers},
            ensure_ascii=False, sort_keys=True,
        )
    return owner_notice(pack).strip()
