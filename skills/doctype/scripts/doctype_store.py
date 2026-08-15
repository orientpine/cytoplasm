"""Immutable, layered document-type registry with private example bodies."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from skills.doctype.scripts import doctype_schema, doctype_sensitivity


_VERSION_FILE: Final = re.compile(r"^v([1-9][0-9]*)\.json$")
_SENSITIVE: Final = "patent-sensitive"


class DocTypeNotFoundError(LookupError):
    """A requested document type has no visible immutable version."""


class DocTypeStorageError(RuntimeError):
    """Private or metadata storage cannot satisfy the registry contract."""


@dataclass(frozen=True, slots=True)
class StorePaths:
    """Explicit roots make canonical, overlay, and private layering testable."""

    canonical_root: Path
    overlay_root: Path
    private_root: Path
    rules_file: Path

    @classmethod
    def from_environment(cls) -> StorePaths:
        repo_root = Path(os.environ.get("DOCTYPE_REPO_ROOT", str(_default_repo_root()))).expanduser()
        skill_root = Path(__file__).resolve().parents[1]
        return cls(
            canonical_root=repo_root / "doctype" / "library",
            overlay_root=Path(
                os.environ.get("DOCTYPE_OVERLAY_ROOT", "~/.hermes/doctype-library/entries")
            ).expanduser(),
            private_root=Path(
                os.environ.get("DOCTYPE_PRIVATE_ROOT", "~/.hermes/doctype/private")
            ).expanduser(),
            rules_file=Path(
                os.environ.get("DOCTYPE_RULES_FILE", str(skill_root / "configs/sensitivity-rules.yaml"))
            ).expanduser(),
        )


@dataclass(frozen=True, slots=True)
class ExampleUpload:
    """A source or approved document that must be persisted outside the repository."""

    data: bytes
    format: str


@dataclass(frozen=True, slots=True)
class DocTypeDraft:
    """Validated semantic knowledge plus exactly one new private example."""

    id: str
    doc_type_name: str
    mode: str
    sections: tuple[doctype_schema.Section, ...]
    fields: tuple[doctype_schema.Field, ...]
    gist: str
    tone: str
    sensitivity: str
    example: ExampleUpload
    template_from_example: bool


@dataclass(frozen=True, slots=True)
class StoredDocType:
    """One selected metadata projection, never including a document body."""

    metadata: doctype_schema.DocTypeMetadata
    path: Path
    source: str


@dataclass(frozen=True, slots=True)
class AddResult:
    """Locations of a new immutable metadata version and its private body."""

    entry: StoredDocType
    path: Path
    private_path: Path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_repo_root() -> Path:
    current = Path("/srv/autophagy-agent-current")  # release runtime (DG-4)
    if current.is_dir():
        return current
    deployed = Path("/srv/autophagy-agents")
    if deployed.is_dir():
        return deployed
    for parent in Path(__file__).resolve().parents:
        if (parent / "skills").is_dir():
            return parent
    return deployed


def _secure_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def _write_exclusive(path: Path, content: str | bytes) -> None:
    mode = "xb" if isinstance(content, bytes) else "x"
    with path.open(mode, encoding=None if isinstance(content, bytes) else "utf-8") as handle:
        _ = handle.write(content)
    path.chmod(0o600)


class DocTypeStore:
    """Read layered versions and append new metadata without destructive replacement."""

    paths: StorePaths
    _clock: Callable[[], str]

    def __init__(self, paths: StorePaths, clock: Callable[[], str] = _now) -> None:
        self.paths = paths
        self._clock = clock

    def _entries_at(self, root: Path, source: str) -> Iterable[StoredDocType]:
        if not root.is_dir():
            return ()
        entries: list[StoredDocType] = []
        for path in sorted(root.glob("*/v*.json")):
            matched = _VERSION_FILE.fullmatch(path.name)
            if matched is None:
                continue
            metadata = doctype_schema.parse_entry(path.read_text(encoding="utf-8"))
            if metadata.id != path.parent.name or metadata.version != int(matched.group(1)):
                raise DocTypeStorageError(f"path/schema mismatch: {path}")
            entries.append(StoredDocType(metadata, path, source))
        return tuple(entries)

    def _entries(self) -> tuple[StoredDocType, ...]:
        selected: dict[tuple[str, int], StoredDocType] = {}
        priorities = {"canonical": 1, "overlay": 2}
        for entry in (*self._entries_at(self.paths.canonical_root, "canonical"), *self._entries_at(self.paths.overlay_root, "overlay")):
            key = (entry.metadata.id, entry.metadata.version)
            prior = selected.get(key)
            if prior is None or priorities[entry.source] > priorities[prior.source]:
                selected[key] = entry
        return tuple(sorted(selected.values(), key=lambda entry: (entry.metadata.id, entry.metadata.version)))

    def list(self) -> tuple[StoredDocType, ...]:
        """List every visible immutable version, with overlay precedence per version."""
        return self._entries()

    def search(self, query: str) -> tuple[StoredDocType, ...]:
        """Find types by body-free name, gist, tone, field, or section metadata."""
        needle = query.strip().casefold()
        if not needle:
            raise DocTypeStorageError("search query must not be empty")
        return tuple(
            entry
            for entry in self._entries()
            if needle in "\n".join(
                (
                    entry.metadata.id,
                    entry.metadata.doc_type_name,
                    entry.metadata.gist,
                    entry.metadata.tone,
                    *(section.title for section in entry.metadata.sections),
                    *(field.name for field in entry.metadata.fields),
                )
            ).casefold()
        )

    def get(self, entry_id: str, version: int | None = None) -> StoredDocType:
        """Return an exact version or the latest version for one stable id."""
        _ = doctype_schema.validate_identifier(entry_id)
        choices = [entry for entry in self._entries() if entry.metadata.id == entry_id]
        if version is not None:
            choices = [entry for entry in choices if entry.metadata.version == version]
        if not choices:
            suffix = f" v{version}" if version is not None else ""
            raise DocTypeNotFoundError(f"document type not found: {entry_id}{suffix}")
        return max(choices, key=lambda entry: entry.metadata.version)

    def get_by_name(self, name: str) -> StoredDocType:
        """Resolve a human document-type name while preserving the hidden stable id."""
        matches = [entry for entry in self._entries() if entry.metadata.doc_type_name == name.strip()]
        if not matches:
            raise DocTypeNotFoundError(f"document type not found: {name}")
        return max(matches, key=lambda entry: entry.metadata.version)

    def example_path(self, reference: doctype_schema.ExampleRef) -> Path:
        """Resolve an opaque private body reference without reading or logging it."""
        opaque = reference.ref.removeprefix("private:")
        path = self.paths.private_root / f"{opaque}.{reference.format}"
        if not path.is_file():
            raise DocTypeStorageError("private document example is unavailable")
        return path

    def _store_example(self, upload: ExampleUpload) -> tuple[doctype_schema.ExampleRef, Path]:
        if upload.format not in ("md", "txt", "docx", "hwpx") or not upload.data:
            raise DocTypeStorageError("example must have a supported non-empty format")
        opaque = secrets.token_hex(16)
        reference = doctype_schema.ExampleRef(
            ref=f"private:{opaque}", sha256=hashlib.sha256(upload.data).hexdigest(), format=upload.format
        )
        _secure_directory(self.paths.private_root)
        path = self.paths.private_root / f"{opaque}.{upload.format}"
        _write_exclusive(path, upload.data)
        return reference, path

    def add(self, draft: DocTypeDraft) -> AddResult:
        """Append the next version and always persist the incoming document privately."""
        _ = doctype_schema.validate_identifier(draft.id)
        if draft.sensitivity not in ("none", _SENSITIVE):
            raise DocTypeStorageError("unsupported sensitivity")
        existing = [entry for entry in self._entries() if entry.metadata.id == draft.id]
        latest = max(existing, key=lambda entry: entry.metadata.version) if existing else None
        example, private_path = self._store_example(draft.example)
        try:
            version = max((entry.metadata.version for entry in existing), default=0) + 1
            created = latest.metadata.created if latest is not None else self._clock()
            sensitivity = _SENSITIVE if draft.sensitivity == _SENSITIVE or (latest is not None and latest.metadata.sensitivity == _SENSITIVE) else "none"
            examples = (*(() if latest is None else latest.metadata.examples), example)
            template_ref = example if draft.template_from_example else (None if latest is None else latest.metadata.template_ref)
            metadata = doctype_schema.DocTypeMetadata(
                id=draft.id,
                version=version,
                doc_type_name=draft.doc_type_name.strip(),
                mode=draft.mode,
                sections=draft.sections,
                fields=draft.fields,
                gist=draft.gist.strip(),
                tone=draft.tone.strip(),
                sensitivity=sensitivity,
                template_ref=template_ref,
                examples=examples,
                created=created,
                updated=self._clock(),
            )
            path = self.paths.overlay_root / metadata.id / f"v{metadata.version}.json"
            _secure_directory(path.parent)
            _write_exclusive(path, doctype_schema.compose_entry(metadata))
        except (FileExistsError, OSError, doctype_schema.DocTypeSchemaError) as error:
            private_path.unlink(missing_ok=True)
            raise DocTypeStorageError("version creation failed; no metadata was replaced") from error
        return AddResult(StoredDocType(metadata, path, "overlay"), path, private_path)

    def add_version(self, draft: DocTypeDraft) -> AddResult:
        """Name the append-only operation explicitly for refinement callers."""
        return self.add(draft)

    def repo_root(self) -> Path:
        """Return the metadata repository boundary where generated bodies are forbidden."""
        return self.paths.canonical_root.parents[1]


def document_sensitivity(text: str, rules_file: Path) -> str:
    """Translate the deterministic gate into the metadata routing tag."""
    verdict = doctype_sensitivity.evaluate(text, doctype_sensitivity.load_rules(rules_file))
    return _SENSITIVE if verdict.sensitive else "none"
