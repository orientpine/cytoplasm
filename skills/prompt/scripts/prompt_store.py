from __future__ import annotations

import os
import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from skills.prompt.scripts import prompt_schema, prompt_sensitivity


_VERSION_FILE: Final = re.compile(r"^v([1-9][0-9]*)\.md$")
_LEGACY_FILE: Final = re.compile(r"^meeting-extraction-v([1-9][0-9]*)\.md$")
_LEGACY_MARKER: Final = "<<<PROMPT>>>"
_SENSITIVE_TAG: Final = "patent-sensitive"


class PromptNotFoundError(LookupError):
    pass


class PromptStorageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StorePaths:
    canonical_root: Path
    overlay_root: Path
    private_root: Path
    rules_file: Path

    @classmethod
    def from_environment(cls) -> StorePaths:
        repo_root = Path(os.environ.get("PROMPT_REPO_ROOT", str(_default_repo_root())))
        return cls(
            canonical_root=repo_root / "prompts" / "library",
            overlay_root=Path(
                os.environ.get("PROMPT_OVERLAY_ROOT", "~/.hermes/prompt-library/entries")
            ).expanduser(),
            private_root=Path(os.environ.get("PROMPT_PRIVATE_ROOT", "~/prompts-private")).expanduser(),
            rules_file=Path(
                os.environ.get("PROMPT_RULES_FILE", str(repo_root / "configs/sensitivity-rules.yaml"))
            ).expanduser(),
        )


@dataclass(frozen=True, slots=True)
class PromptDraft:
    id: str
    category: str
    purpose: str
    model: str
    tags: tuple[str, ...]
    body: str


@dataclass(frozen=True, slots=True)
class StoredPrompt:
    metadata: prompt_schema.PromptMetadata
    body: str
    path: Path
    source: str

    @property
    def routing_tags(self) -> tuple[str, ...]:
        return (_SENSITIVE_TAG,) if self.metadata.sensitivity == _SENSITIVE_TAG else ()


@dataclass(frozen=True, slots=True)
class AddResult:
    entry: StoredPrompt
    path: Path
    private_path: Path | None


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
        if (parent / "prompts" / "README.md").is_file():
            return parent
    return deployed


def _secure_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def _write_exclusive(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        _ = handle.write(content)
    path.chmod(0o600)


class PromptStore:
    paths: StorePaths
    _clock: Callable[[], str]

    def __init__(self, paths: StorePaths, clock: Callable[[], str] = _now) -> None:
        self.paths = paths
        self._clock = clock

    def _canonical_entries(self) -> Iterable[StoredPrompt]:
        if not self.paths.canonical_root.is_dir():
            return ()
        entries: list[StoredPrompt] = []
        for path in sorted(self.paths.canonical_root.glob("*/v*.md")):
            match = _VERSION_FILE.fullmatch(path.name)
            if match is None:
                continue
            metadata, body = prompt_schema.parse_entry(path.read_text(encoding="utf-8"))
            if metadata.id != path.parent.name or metadata.version != int(match.group(1)):
                raise prompt_schema.PromptSchemaError(f"path/schema mismatch: {path}")
            entries.append(StoredPrompt(metadata, body, path, "canonical"))
        return tuple(entries)

    def _overlay_entries(self) -> Iterable[StoredPrompt]:
        if not self.paths.overlay_root.is_dir():
            return ()
        entries: list[StoredPrompt] = []
        for path in sorted(self.paths.overlay_root.glob("*/v*.md")):
            match = _VERSION_FILE.fullmatch(path.name)
            if match is None:
                continue
            metadata, body = prompt_schema.parse_entry(path.read_text(encoding="utf-8"))
            if metadata.id != path.parent.name or metadata.version != int(match.group(1)):
                raise prompt_schema.PromptSchemaError(f"path/schema mismatch: {path}")
            entries.append(StoredPrompt(metadata, body, path, "overlay"))
        return tuple(entries)

    def _legacy_entries(self) -> Iterable[StoredPrompt]:
        legacy_root = self.paths.canonical_root.parent
        if not legacy_root.is_dir():
            return ()
        entries: list[StoredPrompt] = []
        for path in sorted(legacy_root.glob("meeting-extraction-v*.md")):
            match = _LEGACY_FILE.fullmatch(path.name)
            if match is None:
                continue
            raw = path.read_text(encoding="utf-8")
            lines = raw.splitlines()
            marker = next((index for index, line in enumerate(lines) if line.strip() == _LEGACY_MARKER), None)
            if marker is None:
                raise prompt_schema.PromptSchemaError(f"legacy prompt lacks marker: {path}")
            timestamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            metadata = prompt_schema.PromptMetadata(
                id="meeting-extraction",
                version=int(match.group(1)),
                category="task",
                purpose="legacy meeting extraction adapter",
                model="any",
                tags=("legacy", "meeting"),
                created=timestamp,
                updated=timestamp,
                sensitivity="none",
                body_ref="inline",
            )
            entries.append(StoredPrompt(metadata, "\n".join(lines[marker + 1 :]).strip(), path, "legacy"))
        return tuple(entries)

    def _entries(self) -> tuple[StoredPrompt, ...]:
        entries = (*self._canonical_entries(), *self._overlay_entries(), *self._legacy_entries())
        priorities = {"legacy": 0, "canonical": 1, "overlay": 2}
        selected: dict[tuple[str, int], StoredPrompt] = {}
        for entry in entries:
            key = (entry.metadata.id, entry.metadata.version)
            previous = selected.get(key)
            if previous is None or priorities[entry.source] > priorities[previous.source]:
                selected[key] = entry
        return tuple(sorted(selected.values(), key=lambda entry: (entry.metadata.id, entry.metadata.version)))

    def search(self, query: str) -> tuple[StoredPrompt, ...]:
        needle = query.strip().casefold()
        if not needle:
            raise PromptStorageError("search query must not be empty")
        matches: list[StoredPrompt] = []
        for entry in self._entries():
            searchable = "\n".join(
                (
                    entry.metadata.id,
                    entry.metadata.category,
                    entry.metadata.purpose,
                    entry.metadata.model,
                    " ".join(entry.metadata.tags),
                    entry.body,
                )
            ).casefold()
            if needle in searchable:
                matches.append(entry)
        return tuple(matches)

    def get(self, entry_id: str, version: int | None = None) -> StoredPrompt:
        _ = prompt_schema.validate_identifier(entry_id)
        choices = [entry for entry in self._entries() if entry.metadata.id == entry_id]
        if version is not None:
            choices = [entry for entry in choices if entry.metadata.version == version]
        if not choices:
            suffix = f" v{version}" if version is not None else ""
            raise PromptNotFoundError(f"prompt not found: {entry_id}{suffix}")
        selected = max(choices, key=lambda entry: entry.metadata.version)
        if selected.metadata.body_ref == "inline":
            return selected
        opaque_id = selected.metadata.body_ref.removeprefix("private:")
        private_path = self.paths.private_root / f"{opaque_id}.md"
        if not private_path.is_file():
            raise PromptStorageError("private prompt body is unavailable")
        return replace(selected, body=private_path.read_text(encoding="utf-8"))

    def add(self, draft: PromptDraft) -> AddResult:
        _ = prompt_schema.validate_identifier(draft.id)
        provisional = prompt_schema.PromptMetadata(
            id=draft.id,
            version=1,
            category=draft.category,
            purpose=draft.purpose,
            model=draft.model,
            tags=draft.tags,
            created=self._clock(),
            updated=self._clock(),
            sensitivity="none",
            body_ref="inline",
        )
        classified = prompt_sensitivity.evaluate(
            prompt_schema.compose_entry(provisional, draft.body), prompt_sensitivity.load_rules(self.paths.rules_file)
        )
        versions = [entry.metadata.version for entry in self._entries() if entry.metadata.id == draft.id]
        version = max(versions, default=0) + 1
        private_path: Path | None = None
        if classified.sensitive:
            opaque_id = secrets.token_hex(16)
            metadata = replace(
                provisional,
                version=version,
                sensitivity=_SENSITIVE_TAG,
                body_ref=f"private:{opaque_id}",
            )
            _secure_directory(self.paths.private_root)
            private_path = self.paths.private_root / f"{opaque_id}.md"
            _write_exclusive(private_path, draft.body)
            stored_body = ""
        else:
            metadata = replace(provisional, version=version)
            stored_body = draft.body
        path = self.paths.overlay_root / metadata.id / f"v{metadata.version}.md"
        _secure_directory(path.parent)
        try:
            _write_exclusive(path, prompt_schema.compose_entry(metadata, stored_body))
        except FileExistsError as error:
            if private_path is not None:
                private_path.unlink()
            raise PromptStorageError("concurrent version creation detected; retry add") from error
        return AddResult(StoredPrompt(metadata, stored_body, path, "overlay"), path, private_path)
