"""Validated, body-free metadata for immutable document-type versions."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Final


_KEYS: Final = frozenset(
    {
        "id",
        "version",
        "doc_type_name",
        "mode",
        "sections",
        "fields",
        "gist",
        "tone",
        "sensitivity",
        "template_ref",
        "examples",
        "created",
        "updated",
    }
)
_ID: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_OPAQUE: Final = re.compile(r"^private:[0-9a-f]{32}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_TIME: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MODES: Final = frozenset(("slot-fill", "narrative", "hybrid"))
_FORMATS: Final = frozenset(("md", "txt", "docx", "hwpx"))


class DocTypeSchemaError(ValueError):
    """Raised when body-free registry metadata violates its stable contract."""


@dataclass(frozen=True, slots=True)
class Section:
    """One reusable section and the model-facing instruction for it."""

    key: str
    title: str
    guidance: str
    kind: str


@dataclass(frozen=True, slots=True)
class Field:
    """One deterministic input field discovered from the example structure."""

    name: str
    guidance: str
    section: str


@dataclass(frozen=True, slots=True)
class ExampleRef:
    """Opaque pointer to an example body that exists only in the private store."""

    ref: str
    sha256: str
    format: str


@dataclass(frozen=True, slots=True)
class DocTypeMetadata:
    """Versioned public projection; it intentionally contains no document body."""

    id: str
    version: int
    doc_type_name: str
    mode: str
    sections: tuple[Section, ...]
    fields: tuple[Field, ...]
    gist: str
    tone: str
    sensitivity: str
    template_ref: ExampleRef | None
    examples: tuple[ExampleRef, ...]
    created: str
    updated: str


def validate_identifier(value: str) -> str:
    """Validate the filesystem-safe, stable registry identifier."""
    if _ID.fullmatch(value) is None:
        raise DocTypeSchemaError("id must be lowercase kebab-case, 1-64 characters")
    return value


def _timestamp(name: str, value: str) -> str:
    if _TIME.fullmatch(value) is None:
        raise DocTypeSchemaError(f"{name} must be UTC ISO-8601")
    try:
        _ = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise DocTypeSchemaError(f"{name} is not a real timestamp") from error
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DocTypeSchemaError(f"{name} must be a non-empty string")
    return value.strip()


def _mode(value: object) -> str:
    mode = _text(value, "mode")
    if mode not in _MODES:
        raise DocTypeSchemaError("mode must be slot-fill, narrative, or hybrid")
    return mode


def _example(value: object) -> ExampleRef:
    if not isinstance(value, dict) or set(value) != {"ref", "sha256", "format"}:
        raise DocTypeSchemaError("example must contain exactly ref, sha256, format")
    ref = _text(value.get("ref"), "example.ref")
    sha256 = _text(value.get("sha256"), "example.sha256")
    format_name = _text(value.get("format"), "example.format")
    if _OPAQUE.fullmatch(ref) is None or _SHA256.fullmatch(sha256) is None or format_name not in _FORMATS:
        raise DocTypeSchemaError("example reference is invalid")
    return ExampleRef(ref, sha256, format_name)


def _sections(value: object) -> tuple[Section, ...]:
    if not isinstance(value, list) or not value:
        raise DocTypeSchemaError("sections must be a non-empty list")
    sections: list[Section] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"key", "title", "guidance", "kind"}:
            raise DocTypeSchemaError("section must contain exactly key, title, guidance, kind")
        key = validate_identifier(_text(item.get("key"), "section.key"))
        kind = _mode(item.get("kind"))
        sections.append(Section(key, _text(item.get("title"), "section.title"), _text(item.get("guidance"), "section.guidance"), kind))
    if len({item.key for item in sections}) != len(sections):
        raise DocTypeSchemaError("section keys must be unique")
    return tuple(sections)


def _fields(value: object, section_keys: frozenset[str]) -> tuple[Field, ...]:
    if not isinstance(value, list):
        raise DocTypeSchemaError("fields must be a list")
    fields: list[Field] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "guidance", "section"}:
            raise DocTypeSchemaError("field must contain exactly name, guidance, section")
        section = _text(item.get("section"), "field.section")
        if section not in section_keys:
            raise DocTypeSchemaError("field section must refer to a known section")
        fields.append(Field(_text(item.get("name"), "field.name"), _text(item.get("guidance"), "field.guidance"), section))
    if len({item.name for item in fields}) != len(fields):
        raise DocTypeSchemaError("field names must be unique")
    return tuple(fields)


def _metadata(raw: object) -> DocTypeMetadata:
    if not isinstance(raw, dict) or set(raw) != _KEYS:
        raise DocTypeSchemaError("metadata keys do not match the document-type schema")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise DocTypeSchemaError("version must be a positive integer")
    sections = _sections(raw.get("sections"))
    fields = _fields(raw.get("fields"), frozenset(item.key for item in sections))
    sensitivity = _text(raw.get("sensitivity"), "sensitivity")
    if sensitivity not in ("none", "patent-sensitive"):
        raise DocTypeSchemaError("unsupported sensitivity")
    template_raw = raw.get("template_ref")
    template = None if template_raw is None else _example(template_raw)
    examples_raw = raw.get("examples")
    if not isinstance(examples_raw, list) or not examples_raw:
        raise DocTypeSchemaError("examples must contain at least one private example reference")
    examples = tuple(_example(item) for item in examples_raw)
    if template is not None and template not in examples:
        raise DocTypeSchemaError("template_ref must refer to a registered example")
    return DocTypeMetadata(
        id=validate_identifier(_text(raw.get("id"), "id")),
        version=version,
        doc_type_name=_text(raw.get("doc_type_name"), "doc_type_name"),
        mode=_mode(raw.get("mode")),
        sections=sections,
        fields=fields,
        gist=_text(raw.get("gist"), "gist"),
        tone=_text(raw.get("tone"), "tone"),
        sensitivity=sensitivity,
        template_ref=template,
        examples=examples,
        created=_timestamp("created", _text(raw.get("created"), "created")),
        updated=_timestamp("updated", _text(raw.get("updated"), "updated")),
    )


def parse_entry(text: str) -> DocTypeMetadata:
    """Parse one metadata-only JSON entry without ever accepting a body field."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise DocTypeSchemaError("invalid document-type metadata JSON") from error
    return _metadata(raw)


def compose_entry(metadata: DocTypeMetadata) -> str:
    """Validate and serialize metadata with stable, body-free JSON ordering."""
    checked = _metadata(json.loads(json.dumps(asdict(metadata), ensure_ascii=False)))
    return json.dumps(asdict(checked), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
