"""Immutable proposal versions with atomic promotion and run-key idempotency."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterator, Sequence

_SLUG: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_VERSION: Final = re.compile(r"^v[0-9]{6}$")
_RUN_KEY: Final = re.compile(r"^[0-9a-f]{64}$")
_SUBDIRECTORIES: Final = ("inputs", "corpus", "images", "out")


class VersionError(RuntimeError):
    """The version store contract was violated."""


class InvalidSlug(VersionError):
    """A slug could escape or alias its private workspace."""


class VersionLocked(VersionError):
    """Another process owns the proposal version lock."""


class HeadCasConflict(VersionError):
    """HEAD changed while a staged version was being promoted."""


@dataclass(frozen=True, slots=True)
class Staging:
    """A private run directory that has not been published."""

    run_key: str
    path: Path


@dataclass(frozen=True, slots=True)
class Reused:
    """An already-published version for an identical run key."""

    run_key: str
    version: str
    path: Path


class VersionStore:
    """Manage immutable proposal versions below an explicit private root."""

    def __init__(self, root: Path) -> None:
        os.umask(0o077)
        # Normalize once so symlinked ancestors (such as a symlinked home) are allowed
        # without making later containment checks compare canonical and lexical paths.
        self.root = root.expanduser().resolve()
        self._private_directory(self.root)

    @classmethod
    def from_environment(cls) -> VersionStore:
        return cls(Path(os.environ.get("PROPOSAL_ROOT", "~/proposals")))

    @staticmethod
    def _private_directory(path: Path) -> None:
        if path.is_symlink():
            raise VersionError(f"symlink directory is not allowed: {path}")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.is_dir() or path.is_symlink():
            raise VersionError(f"private directory is invalid: {path}")
        path.chmod(0o700)

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        if path.is_symlink():
            raise VersionError(f"refusing to replace symlink: {path}")
        VersionStore._private_directory(path.parent)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise

    def resolve_slug_dir(self, slug: str) -> Path:
        """Validate a slug and reject an existing symlink workspace."""
        if _SLUG.fullmatch(slug) is None or slug in {".", ".."}:
            raise InvalidSlug("INVALID-SLUG")
        slug_dir = self.root / slug
        if slug_dir.is_symlink():
            raise InvalidSlug("INVALID-SLUG: symlink component")
        try:
            slug_dir.relative_to(self.root)
        except ValueError as error:
            raise InvalidSlug("INVALID-SLUG") from error
        return slug_dir

    def _layout(self, slug: str) -> Path:
        slug_dir = self.resolve_slug_dir(slug)
        self._private_directory(slug_dir)
        for name in ("versions", "locks", "staging"):
            self._private_directory(slug_dir / name)
        return slug_dir

    def head(self, slug: str) -> str | None:
        slug_dir = self._layout(slug)
        path = slug_dir / "HEAD"
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise VersionError("HEAD is not a regular file")
        value = path.read_text(encoding="utf-8").strip()
        if _VERSION.fullmatch(value) is None:
            raise VersionError("HEAD contains an invalid version")
        if not (slug_dir / "versions" / value).is_dir():
            raise VersionError("HEAD references a missing version")
        return value

    def compute_run_key(
        self,
        parent_manifest_sha256: str,
        delta_hashes: Sequence[str],
        directives: object,
        template_sha256: str,
        profile: str,
        pins: object,
    ) -> str:
        payload = {
            "delta_hashes": sorted(delta_hashes),
            "directives": directives,
            "parent_manifest_sha256": parent_manifest_sha256,
            "pins": pins,
            "profile": profile,
            "template_sha256": template_sha256,
        }
        return hashlib.sha256(self._canonical_json(payload).encode("utf-8")).hexdigest()

    def _version_directories(self, slug: str) -> list[Path]:
        versions = self._layout(slug) / "versions"
        result: list[Path] = []
        for path in versions.iterdir():
            if path.is_symlink():
                raise VersionError(f"symlink version is not allowed: {path.name}")
            if path.is_dir() and _VERSION.fullmatch(path.name):
                result.append(path)
        return sorted(result, key=lambda item: item.name)

    def _read_manifest(self, path: Path) -> dict[str, object]:
        if path.is_symlink() or not path.is_file():
            raise VersionError(f"manifest is not a regular file: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise VersionError(f"manifest is invalid: {path}") from error
        if not isinstance(value, dict):
            raise VersionError(f"manifest is invalid: {path}")
        return value

    def manifest_sha256(self, slug: str, version: str | None) -> str:
        if version is None or _VERSION.fullmatch(version) is None:
            raise VersionError("version is invalid")
        path = self._layout(slug) / "versions" / version / "manifest.json"
        if path.is_symlink() or not path.is_file():
            raise VersionError("version manifest is missing")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def find_by_run_key(self, slug: str, run_key: str) -> str | None:
        if _RUN_KEY.fullmatch(run_key) is None:
            raise VersionError("run key is invalid")
        for version_dir in self._version_directories(slug):
            manifest = self._read_manifest(version_dir / "manifest.json")
            if manifest.get("run_key") == run_key:
                return version_dir.name
        return None

    def begin(self, slug: str, run_key: str) -> Staging | Reused:
        slug_dir = self._layout(slug)
        reused = self.find_by_run_key(slug, run_key)
        if reused is not None:
            return Reused(run_key, reused, slug_dir / "versions" / reused)
        path = slug_dir / "staging" / run_key
        self._private_directory(path)
        for name in _SUBDIRECTORIES:
            self._private_directory(path / name)
        return Staging(run_key, path)

    @staticmethod
    def _validate_manifest(manifest: dict[str, object]) -> None:
        forbidden = {"timestamp", "created_at", "updated_at", "created", "updated"}
        if forbidden.intersection(manifest):
            raise VersionError("manifest timestamps are forbidden")
        parent = manifest.get("parent")
        if parent is not None and (
            not isinstance(parent, str) or _VERSION.fullmatch(parent) is None
        ):
            raise VersionError("manifest parent is invalid")
        schema_version = manifest.get("schema_version", 1)
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise VersionError("manifest schema_version is invalid")

    def promote(self, slug: str, staging: Staging, manifest: dict[str, object]) -> str:
        """Publish staging while holding the fail-fast per-slug lock."""
        slug_dir = self._layout(slug)
        with self.lock(slug):
            expected_path = slug_dir / "staging" / staging.run_key
            if (
                staging.path != expected_path
                or staging.path.is_symlink()
                or not staging.path.is_dir()
            ):
                raise VersionError("staging directory is invalid")
            self._validate_manifest(manifest)
            expected_parent = manifest.get("parent")
            if self.head(slug) != expected_parent:
                raise HeadCasConflict("HEAD compare-and-swap conflict")

            versions = self._version_directories(slug)
            next_number = max((int(path.name[1:]) for path in versions), default=0) + 1
            version = f"v{next_number:06d}"
            destination = slug_dir / "versions" / version
            published_manifest = dict(manifest)
            published_manifest.update(
                {
                    "parent": expected_parent,
                    "run_key": staging.run_key,
                    "schema_version": manifest.get("schema_version", 1),
                    "version": version,
                }
            )
            self._atomic_write(
                staging.path / "manifest.json",
                (self._canonical_json(published_manifest) + "\n").encode("utf-8"),
            )

            os.replace(staging.path, destination)
            destination.chmod(0o700)
            head_path = slug_dir / "HEAD"
            temporary_head = slug_dir / f".HEAD.{staging.run_key}"
            self._atomic_write(temporary_head, f"{version}\n".encode("utf-8"))
            os.replace(temporary_head, head_path)
            head_path.chmod(0o600)
            return version

    def abort(self, slug: str, staging: Staging) -> None:
        expected = self._layout(slug) / "staging" / staging.run_key
        if staging.path != expected or staging.path.is_symlink():
            raise VersionError("staging directory is invalid")
        if staging.path.exists():
            shutil.rmtree(staging.path)

    def _changelog_entries(self, slug: str) -> list[object]:
        path = self._layout(slug) / "changelog.json"
        if not path.exists():
            return []
        value = self._read_json_file(path)
        if not isinstance(value, list):
            raise VersionError("changelog.json must contain a list")
        return value

    @staticmethod
    def _read_json_file(path: Path) -> object:
        if path.is_symlink() or not path.is_file():
            raise VersionError(f"JSON file is not regular: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise VersionError(f"JSON file is invalid: {path}") from error

    def append_changelog(self, slug: str, entry: object) -> None:
        slug_dir = self._layout(slug)
        entries = self._changelog_entries(slug)
        entries.append(entry)
        self._atomic_write(
            slug_dir / "changelog.json",
            (self._canonical_json(entries) + "\n").encode("utf-8"),
        )
        self.regenerate_changelog(slug)

    def regenerate_changelog(self, slug: str) -> None:
        slug_dir = self._layout(slug)
        entries = self._changelog_entries(slug)
        lines = ["# Changelog", ""]
        for index, entry in enumerate(entries, start=1):
            if isinstance(entry, dict):
                heading = str(entry.get("version", f"Entry {index}"))
                lines.extend((f"## {heading}", ""))
                changes = entry.get("changes", [])
                if isinstance(changes, list):
                    lines.extend(f"- {change}" for change in changes)
                else:
                    lines.append(f"- {changes}")
            else:
                lines.extend((f"## Entry {index}", "", f"- {entry}"))
            lines.append("")
        self._atomic_write(slug_dir / "CHANGELOG.md", "\n".join(lines).encode("utf-8"))

    @contextmanager
    def lock(self, slug: str) -> Iterator[None]:
        lock_path = self._layout(slug) / "locks" / "version.lock"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except FileExistsError as error:
            raise VersionLocked("proposal version is locked") from error
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(descriptor)
            os.close(descriptor)
            lock_path.chmod(0o600)
            yield
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            lock_path.unlink(missing_ok=True)


def _parse_directives(values: Sequence[str]) -> dict[str, str]:
    directives: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise VersionError("directive must use K=V")
        directives[key] = item
    return directives


def _request_matches(store: VersionStore, slug: str, request: dict[str, object]) -> Reused | None:
    current = store.head(slug)
    if current is None:
        return None
    version_path = store.resolve_slug_dir(slug) / "versions" / current
    manifest = store._read_manifest(version_path / "manifest.json")
    run_key = manifest.get("run_key")
    if manifest.get("request") == request and isinstance(run_key, str):
        return Reused(run_key, current, version_path)
    return None


def _result(
    slug: str, version: str, run_key: str, reused: bool, head: str | None
) -> dict[str, object]:
    return {"slug": slug, "version": version, "run_key": run_key, "reused": reused, "head": head}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--slug", required=True)
    create.add_argument("--delta-hash", action="append", default=[])
    create.add_argument("--directive", action="append", default=[])
    create.add_argument("--profile", default="30-page")
    create.add_argument("--json", action="store_true")
    status = subparsers.add_parser("status")
    status.add_argument("--slug", required=True)
    status.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = VersionStore.from_environment()
    try:
        if args.command == "status":
            payload = {"slug": args.slug, "head": store.head(args.slug)}
        else:
            directives = _parse_directives(args.directive)
            request: dict[str, object] = {
                "delta_hashes": sorted(args.delta_hash),
                "directives": directives,
                "pins": {},
                "profile": args.profile,
                "template_sha256": "",
            }
            reused = _request_matches(store, args.slug, request)
            if reused is not None:
                payload = _result(
                    args.slug, reused.version, reused.run_key, True, store.head(args.slug)
                )
            else:
                parent = store.head(args.slug)
                parent_sha = store.manifest_sha256(args.slug, parent) if parent else ""
                run_key = store.compute_run_key(
                    parent_sha,
                    args.delta_hash,
                    directives,
                    "",
                    args.profile,
                    {},
                )
                staging = store.begin(args.slug, run_key)
                if isinstance(staging, Reused):
                    payload = _result(
                        args.slug, staging.version, run_key, True, store.head(args.slug)
                    )
                else:
                    version = store.promote(
                        args.slug,
                        staging,
                        {"parent": parent, "request": request, "schema_version": 1},
                    )
                    payload = _result(args.slug, version, run_key, False, store.head(args.slug))
        if getattr(args, "json", False):
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            print(payload)
        return 0
    except InvalidSlug as error:
        print(f"INVALID-SLUG: {error}", file=sys.stderr)
        return 2
    except VersionLocked as error:
        print(str(error), file=sys.stderr)
        return 3
    except VersionError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
