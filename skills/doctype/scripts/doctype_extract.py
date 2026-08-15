"""Private example reading plus deterministic structure and Codex gist extraction."""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as element_tree
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Final

from skills.doctype.scripts import doctype_llm, doctype_schema, doctype_sensitivity, doctype_store


_HEADING: Final = re.compile(r"^\s*(?:#{2,6}\s+|(?:\d+|[가-힣])[.)]\s+)(.+?)\s*$")
_SLOT: Final = re.compile(r"\{\{([^{}]{1,60})\}\}")
_LABEL: Final = re.compile(r"^\s*(?:[-*]\s*)?([^:\n]{1,40}):\s*(?:\{\{[^{}]+\}\}|.+)?$")
_MODES: Final = frozenset(("slot-fill", "narrative", "hybrid"))


class ExtractionError(RuntimeError):
    """An example document cannot become safe reusable type knowledge."""


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """A private example in both original bytes and text usable for local analysis."""

    path: Path
    format: str
    data: bytes
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedType:
    """Semantic knowledge emitted without retaining the source document body."""

    mode: str
    sections: tuple[doctype_schema.Section, ...]
    fields: tuple[doctype_schema.Field, ...]
    gist: str
    tone: str
    sensitivity: str
    source: SourceDocument
    template_from_example: bool

    def draft(self, entry_id: str, doc_type_name: str) -> doctype_store.DocTypeDraft:
        """Convert extracted knowledge into the append-only registry input."""
        return doctype_store.DocTypeDraft(
            id=entry_id,
            doc_type_name=doc_type_name,
            mode=self.mode,
            sections=self.sections,
            fields=self.fields,
            gist=self.gist,
            tone=self.tone,
            sensitivity=self.sensitivity,
            example=doctype_store.ExampleUpload(self.source.data, self.source.format),
            template_from_example=self.template_from_example,
        )


def _container_format(path: Path) -> str | None:
    try:
        from skills.procurement.scripts import procure_core

        return procure_core.preflight_path(path).format
    except ImportError:
        return None
    except (OSError, ValueError):
        return None


def _hwpx_text(data: bytes) -> str:
    chunks: list[str] = []
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            for member in archive.namelist():
                if member.startswith("Contents/") and member.endswith(".xml"):
                    chunks.extend(value.strip() for value in element_tree.fromstring(archive.read(member)).itertext() if value.strip())
    except (element_tree.ParseError, zipfile.BadZipFile) as error:
        raise ExtractionError("HWPX text extraction failed") from error
    return "\n".join(chunks)


def read_document(path: Path) -> SourceDocument:
    """Read supported private examples using procurement readers when available."""
    if not path.is_file():
        raise ExtractionError(f"example file is missing: {path}")
    data = path.read_bytes()
    suffix = path.suffix.lower().removeprefix(".")
    container = _container_format(path) if suffix in ("docx", "hwpx") else None
    format_name = container or suffix
    if format_name == "docx":
        try:
            from skills.procurement.scripts import procure_docx

            text = procure_docx.text_content(path)
        except ImportError as error:
            raise ExtractionError("DOCX support requires the co-deployed procurement engine") from error
    elif format_name == "hwpx":
        text = _hwpx_text(data)
    elif format_name in ("md", "txt"):
        text = data.decode("utf-8", errors="replace")
    else:
        raise ExtractionError("example format must be .md, .txt, .docx, or .hwpx")
    if not text.strip():
        raise ExtractionError("example document has no extractable text")
    return SourceDocument(path, format_name, data, text)


def _key(index: int) -> str:
    return f"section-{index:02d}"


def _deterministic_structure(text: str) -> tuple[tuple[doctype_schema.Section, ...], tuple[doctype_schema.Field, ...]]:
    titles = [matched.group(1).strip() for line in text.splitlines() if (matched := _HEADING.match(line))]
    titles = titles or ["본문"]
    sections = tuple(doctype_schema.Section(_key(index), title, "예시의 순서와 목적을 유지한다.", "hybrid") for index, title in enumerate(titles, start=1))
    current = sections[0].key
    fields: list[doctype_schema.Field] = []
    seen: set[str] = set()
    section_index = 0
    for line in text.splitlines():
        if _HEADING.match(line):
            current = sections[min(section_index, len(sections) - 1)].key
            section_index += 1
        slots = tuple(item.strip() for item in _SLOT.findall(line) if item.strip())
        label_match = _LABEL.match(line)
        labels = () if label_match is None else (label_match.group(1).strip(),)
        for name in (*slots, *labels):
            if name not in seen:
                seen.add(name)
                fields.append(doctype_schema.Field(name, f"{name}의 사실값을 제공한다.", current))
    return sections, tuple(fields)


def _json_object(raw: str) -> dict[str, object]:
    start = raw.find("{")
    if start < 0:
        raise ExtractionError("Codex gist response lacks JSON")
    try:
        parsed, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as error:
        raise ExtractionError("Codex gist response has invalid JSON") from error
    if not isinstance(parsed, dict):
        raise ExtractionError("Codex gist response must be an object")
    return parsed


def _semantic_sections(
    payload: dict[str, object], deterministic: tuple[doctype_schema.Section, ...]
) -> tuple[doctype_schema.Section, ...]:
    raw_sections = payload.get("sections")
    by_title: dict[str, tuple[str, str]] = {}
    if isinstance(raw_sections, list):
        for item in raw_sections:
            if isinstance(item, dict):
                title, guidance, kind = item.get("title"), item.get("guidance"), item.get("kind")
                if isinstance(title, str) and isinstance(guidance, str) and isinstance(kind, str) and kind in _MODES:
                    by_title[title.strip()] = (guidance.strip() or "예시 목적을 유지한다.", kind)
    merged = tuple(
        doctype_schema.Section(
            section.key,
            section.title,
            by_title.get(section.title, (section.guidance, section.kind))[0],
            by_title.get(section.title, (section.guidance, section.kind))[1],
        )
        for section in deterministic
    )
    return merged


def _semantic_prompt(source: SourceDocument, prior: doctype_schema.DocTypeMetadata | None, note: str) -> str:
    prior_text = "" if prior is None else f"\nPRIOR_GIST={prior.gist}\nPRIOR_TONE={prior.tone}\n"
    return (
        "DOCTYPE_STAGE=EXTRACT\n"
        "Return JSON only: {\"gist\":str,\"tone\":str,\"mode\":\"slot-fill|narrative|hybrid\","
        "\"sections\":[{\"title\":str,\"guidance\":str,\"kind\":\"slot-fill|narrative|hybrid\"}]}.\n"
        "Infer reusable Korean public-document structure, purpose, tone, and reasoning pattern. "
        "Do not reproduce the example verbatim.\n"
        f"REFINEMENT_NOTE={note.strip()}\n{prior_text}EXAMPLE:\n{source.text}"
    )


def extract(
    path: Path,
    rules_file: Path,
    *,
    mode_override: str | None = None,
    prior: doctype_schema.DocTypeMetadata | None = None,
    note: str = "",
) -> ExtractedType:
    """Gate before Codex, then combine semantic analysis with deterministic layout facts."""
    source = read_document(path)
    verdict = doctype_sensitivity.evaluate(source.text, doctype_sensitivity.load_rules(rules_file))
    deterministic_sections, fields = _deterministic_structure(source.text)
    opaque_id = hashlib.sha256(source.data).hexdigest()[:16]
    payload = _json_object(
        doctype_llm.call_codex(
            _semantic_prompt(source, prior, note), purpose="gist-extract", sensitive=verdict.sensitive, opaque_id=opaque_id
        )
    )
    gist = payload.get("gist")
    tone = payload.get("tone")
    inferred = payload.get("mode")
    if not isinstance(gist, str) or not gist.strip() or not isinstance(tone, str) or not tone.strip():
        raise ExtractionError("Codex gist response requires gist and tone")
    mode = mode_override or inferred
    if not isinstance(mode, str) or mode not in _MODES:
        raise ExtractionError("Codex gist response has unsupported mode")
    template = source.format in ("docx", "hwpx") and bool(_SLOT.search(source.text))
    return ExtractedType(
        mode=mode,
        sections=_semantic_sections(payload, deterministic_sections),
        fields=fields,
        gist=gist.strip(),
        tone=tone.strip(),
        sensitivity="patent-sensitive" if verdict.sensitive else "none",
        source=source,
        template_from_example=template,
    )
