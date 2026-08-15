from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final


_KEYS: Final = (
    "id",
    "version",
    "category",
    "purpose",
    "model",
    "tags",
    "created",
    "updated",
    "sensitivity",
    "body_ref",
)
_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TIMESTAMP_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_PRIVATE_REF_RE: Final = re.compile(r"^private:[0-9a-f]{32}$")
_CATEGORIES: Final = frozenset(("task", "research-background"))
_MODELS: Final = frozenset(("glm-main", "openai-codex", "any"))
_SENSITIVITIES: Final = frozenset(("none", "patent-sensitive"))


class PromptSchemaError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PromptMetadata:
    id: str
    version: int
    category: str
    purpose: str
    model: str
    tags: tuple[str, ...]
    created: str
    updated: str
    sensitivity: str
    body_ref: str


def validate_identifier(value: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise PromptSchemaError(f"invalid prompt id: {value!r}")
    return value


def _scalar(raw: str) -> str:
    value = raw.strip()
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            raise PromptSchemaError("invalid quoted scalar")
        escaped = value[1:-1]
        if "\n" in escaped or "\r" in escaped:
            raise PromptSchemaError("quoted scalar must stay on one line")
        protected = escaped.replace("\\\\", "\0")
        unquoted = protected.replace('\\"', '"').replace("\0", "\\")
        if "\\" in unquoted:
            raise PromptSchemaError("unsupported quoted scalar escape")
        return unquoted
    return value


def _tags(raw: str) -> tuple[str, ...]:
    value = raw.strip()
    if not value.startswith("[") or not value.endswith("]"):
        raise PromptSchemaError("tags must be a flow list")
    inner = value[1:-1].strip()
    if not inner:
        return ()
    tags = tuple(_scalar(item) for item in inner.split(","))
    if any(not tag or any(char.isspace() for char in tag) for tag in tags):
        raise PromptSchemaError("tags must be non-empty strings without whitespace")
    return tags


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _timestamp(name: str, value: str) -> None:
    if not _TIMESTAMP_RE.fullmatch(value):
        raise PromptSchemaError(f"{name} must be UTC ISO-8601")
    try:
        _ = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise PromptSchemaError(f"{name} is not a real timestamp") from error


def _header_values(header: str) -> dict[str, str | tuple[str, ...]]:
    values: dict[str, str | tuple[str, ...]] = {}
    for line in header.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise PromptSchemaError("frontmatter lines must be key: value")
        key, _, raw = line.partition(":")
        if key in values:
            raise PromptSchemaError(f"duplicate frontmatter key: {key}")
        values[key] = _tags(raw) if key == "tags" else _scalar(raw)
    expected: set[str] = set(_KEYS)
    actual: set[str] = set(values)
    if actual != expected or len(values) != len(_KEYS):
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise PromptSchemaError(f"frontmatter must have exactly 10 keys missing={missing} extra={extra}")
    return values


def _metadata(values: dict[str, str | tuple[str, ...]]) -> PromptMetadata:
    raw_version = values["version"]
    if not isinstance(raw_version, str) or not raw_version.isdecimal() or int(raw_version) < 1:
        raise PromptSchemaError("version must be a positive integer")
    raw_tags = values["tags"]
    if not isinstance(raw_tags, tuple):
        raise PromptSchemaError("tags must be a list")
    scalar_keys = tuple(key for key in _KEYS if key not in ("tags", "version"))
    if any(not isinstance(values[key], str) for key in scalar_keys):
        raise PromptSchemaError("frontmatter scalar type mismatch")
    entry_id = validate_identifier(str(values["id"]))
    category = str(values["category"])
    model = str(values["model"])
    sensitivity = str(values["sensitivity"])
    body_ref = str(values["body_ref"])
    purpose = str(values["purpose"])
    if category not in _CATEGORIES:
        raise PromptSchemaError("unsupported category")
    if model not in _MODELS:
        raise PromptSchemaError("unsupported model")
    if not purpose:
        raise PromptSchemaError("purpose must not be empty")
    if sensitivity not in _SENSITIVITIES:
        raise PromptSchemaError("unsupported sensitivity")
    _timestamp("created", str(values["created"]))
    _timestamp("updated", str(values["updated"]))
    if sensitivity == "none" and body_ref != "inline":
        raise PromptSchemaError("non-sensitive entries must use body_ref: inline")
    if sensitivity == "patent-sensitive" and not _PRIVATE_REF_RE.fullmatch(body_ref):
        raise PromptSchemaError("sensitive entries require an opaque private body_ref")
    return PromptMetadata(
        id=entry_id,
        version=int(raw_version),
        category=category,
        purpose=purpose,
        model=model,
        tags=raw_tags,
        created=str(values["created"]),
        updated=str(values["updated"]),
        sensitivity=sensitivity,
        body_ref=body_ref,
    )


def parse_entry(text: str) -> tuple[PromptMetadata, str]:
    if not text.startswith("---\n"):
        raise PromptSchemaError("frontmatter must start with ---")
    header_end = text.find("\n---\n", 4)
    if header_end < 0:
        raise PromptSchemaError("frontmatter closing delimiter is missing")
    metadata = _metadata(_header_values(text[4:header_end]))
    body = text[header_end + 5 :]
    if metadata.body_ref != "inline" and body:
        raise PromptSchemaError("private-body metadata stubs must have a zero-byte body")
    return metadata, body


def compose_entry(metadata: PromptMetadata, body: str) -> str:
    checked = _metadata(
        {
            "id": metadata.id,
            "version": str(metadata.version),
            "category": metadata.category,
            "purpose": metadata.purpose,
            "model": metadata.model,
            "tags": metadata.tags,
            "created": metadata.created,
            "updated": metadata.updated,
            "sensitivity": metadata.sensitivity,
            "body_ref": metadata.body_ref,
        }
    )
    content = body.rstrip("\n")
    if checked.body_ref != "inline" and content:
        raise PromptSchemaError("private-body metadata stubs must have a zero-byte body")
    lines = (
        "---",
        f"id: {checked.id}",
        f"version: {checked.version}",
        f"category: {checked.category}",
        f"purpose: {_quoted(checked.purpose)}",
        f"model: {checked.model}",
        "tags: [" + ", ".join(checked.tags) + "]",
        f"created: {checked.created}",
        f"updated: {checked.updated}",
        f"sensitivity: {checked.sensitivity}",
        f"body_ref: {checked.body_ref}",
        "---",
    )
    return "\n".join(lines) + "\n" + (content + "\n" if content else "")
