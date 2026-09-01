"""Publish a complete proposal version tree through verified owner-only Drive uploads."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast

from .proposal_route_guard import RouteRefused, assert_route_allowed, classify

_VERSION: Final = re.compile(r"^v[0-9]{6}$")
_RECEIPT: Final = "publish-receipt.json"
_MANIFEST: Final = "manifest.json"
_DRIVE_LINK_THRESHOLD: Final = 25 * 1024 * 1024


def _root_folder() -> str:
    """The single Drive root, honouring the same override `drive_taxonomy` reads.

    The default is still spelled twice: importing `automation.drive_taxonomy` from a
    mounted skill would drag in the runtime-root import boundary this module has
    deliberately stayed outside of (it takes its transport by injection). Reading the
    same environment variable closes the half that can split silently — otherwise a
    rename that moves every other skill leaves proposal writing to a stale root.
    """
    return os.environ.get("DRIVE_OUTPUTS_ROOT") or "autophagy"


class PublishError(RuntimeError):
    """A publication could not finish and must not be reported as successful."""

    exit_code: int = 1


class PublishPermissionError(PublishError):
    """Drive permissions were not owner-only."""

    exit_code: int = 2


class PublishShaMismatch(PublishError):
    """Drive read-back did not match the local artifact."""

    exit_code: int = 3


class PublishBoundaryError(PublishError):
    """The version tree violated a local privacy or filesystem boundary."""

    exit_code: int = 4


class PublishDraftPreviewError(PublishError):
    """A draft preview manifest is not publishable."""

    exit_code: int = 5


class DriveTransport(Protocol):
    """Narrow seam shared by the live Drive client and the persistent test fake."""

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str: ...

    def upsert_file(
        self, local: Path, name: str, parent_id: str, prior_id: str | None = None
    ) -> dict[str, str]: ...

    def verify_owner_only(self, file_id: str) -> None: ...

    def download_and_verify(self, file_id: str, local: Path) -> str: ...


@dataclass(frozen=True, slots=True)
class PublishedFile:
    path: str
    id: str
    sha256: str
    web_view_link: str
    size: int


@dataclass(frozen=True, slots=True)
class PublishResult:
    slug: str
    version: str
    files: tuple[PublishedFile, ...]
    uploads: tuple[PublishedFile, ...]
    receipt: Path


class FakeDriveTransport:
    """Persistent fake Drive used by unit tests and exact offline manual QA."""

    def __init__(self, state_file: Path, failure: str | None = None) -> None:
        self.state_file = state_file
        self.failure = failure
        self.calls: list[tuple[str, str]] = []
        self._folder_parts: tuple[str, ...] = ()
        self._upserts = 0
        self._state = self._load()

    def _load(self) -> dict[str, object]:
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"next_id": 1, "files": {}}
        return value if isinstance(value, dict) else {"next_id": 1, "files": {}}

    def _save(self) -> None:
        self.state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _atomic_write(self.state_file, _json_bytes(self._state))

    def _files(self) -> dict[str, dict[str, str]]:
        files = self._state.setdefault("files", {})
        if not isinstance(files, dict):
            raise PublishError("fake Drive state has invalid files")
        return cast(dict[str, dict[str, str]], files)

    def _relative_path(self, name: str) -> str:
        directory = self._folder_parts[4:]
        return "/".join((*directory, name))

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str:
        self._folder_parts = parts
        self.calls.append(("ensure_folder_path", "/".join(parts)))
        return "folder:" + "/".join(parts)

    def upsert_file(
        self, local: Path, name: str, parent_id: str, prior_id: str | None = None
    ) -> dict[str, str]:
        del parent_id
        relative = self._relative_path(name)
        if self.calls and self.calls[-1][0] == "ensure_folder_path":
            self.calls[-1] = ("ensure_folder_path", relative)
        self.calls.append(("upsert_file", relative))
        self._upserts += 1
        if self.failure == "mid-tree" and self._upserts == 2:
            raise PublishError("fake mid-tree interruption")
        if self.failure == "receipt" and relative == _RECEIPT:
            raise PublishError("fake receipt upload failure")
        files = self._files()
        existing = files.get(relative, {})
        raw_next_id = self._state.get("next_id", 1)
        next_id = raw_next_id if isinstance(raw_next_id, int) else 1
        file_id = prior_id or existing.get("id") or f"fake-file-{next_id:06d}"
        if not prior_id and not existing.get("id"):
            self._state["next_id"] = next_id + 1
        files[relative] = {
            "id": file_id,
            "bytes": base64.b64encode(local.read_bytes()).decode("ascii"),
        }
        self._save()
        return {
            "id": file_id,
            "webViewLink": f"https://drive.example.invalid/file/{file_id}",
            "action": "updated" if existing else "created",
        }

    def _path_for_id(self, file_id: str) -> str:
        for path, record in self._files().items():
            if record.get("id") == file_id:
                return path
        raise PublishError(f"fake Drive id is absent: {file_id}")

    def verify_owner_only(self, file_id: str) -> None:
        path = self._path_for_id(file_id)
        self.calls.append(("verify_owner_only", path))
        if self.failure == "public-perm":
            raise PublishPermissionError(f"public permission refused: {file_id}")

    def download_and_verify(self, file_id: str, local: Path) -> str:
        path = self._path_for_id(file_id)
        self.calls.append(("download_and_verify", path))
        expected = _sha256(local)
        remote = hashlib.sha256(base64.b64decode(self._files()[path]["bytes"])).hexdigest()
        if self.failure == "sha-mismatch":
            remote = "0" * 64
        if remote != expected:
            raise PublishShaMismatch(
                f"Drive read-back sha256 mismatch: {path} local={expected} remote={remote}"
            )
        return remote

    def file_for_path(self, relative: str) -> dict[str, str]:
        return dict(self._files()[relative])

    def uploaded_bytes(self, relative: str) -> bytes | None:
        record = self._files().get(relative)
        return None if record is None else base64.b64decode(record["bytes"])

    def all_uploaded_bytes(self) -> bytes:
        return b"".join(base64.b64decode(record["bytes"]) for record in self._files().values())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _atomic_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise PublishBoundaryError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
        temporary.unlink(missing_ok=True)
        raise


def _version_directory(root: Path, slug: str, version: str) -> Path:
    if _VERSION.fullmatch(version) is None:
        raise PublishBoundaryError(f"invalid version: {version}")
    if not slug or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in slug):
        raise PublishBoundaryError(f"invalid slug: {slug}")
    path = root.expanduser().resolve() / slug / "versions" / version
    if path.is_symlink() or not path.is_dir():
        raise PublishBoundaryError(f"version directory is missing or invalid: {path}")
    return path


def _walk_files(version_dir: Path) -> tuple[Path, ...]:
    raw = version_dir / "delta" / "raw"
    if raw.exists() and not (version_dir / "delta" / "INDEX.json").is_file():
        raise PublishBoundaryError(
            "delta/raw owner-private bytes require replacement delta/INDEX.json"
        )
    discovered: list[Path] = []
    for current, directories, filenames in os.walk(version_dir, followlinks=False):
        current_path = Path(current)
        for name in sorted(directories):
            candidate = current_path / name
            if candidate.is_symlink():
                raise PublishBoundaryError(f"symlink directory is not publishable: {candidate}")
        directories[:] = sorted(
            name
            for name in directories
            if not (current_path / name).is_symlink()
            and (current_path / name) != raw
        )
        for name in sorted(filenames):
            candidate = current_path / name
            if candidate.is_symlink():
                raise PublishBoundaryError(f"symlink file is not publishable: {candidate}")
            relative = candidate.relative_to(version_dir).as_posix()
            if relative not in {_MANIFEST, _RECEIPT}:
                discovered.append(candidate)
    return tuple(sorted(discovered, key=lambda path: path.relative_to(version_dir).as_posix()))


def _manifest_base(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublishBoundaryError(f"manifest is missing or malformed: {path}") from error
    if not isinstance(value, dict):
        raise PublishBoundaryError(f"manifest is not a JSON object: {path}")
    value.pop("files", None)
    return cast(dict[str, object], value)


def _prior_inventory(path: Path) -> dict[str, dict[str, str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = value.get("files") if isinstance(value, dict) else None
    if not isinstance(files, dict):
        return {}
    return {
        str(name): {str(key): str(item) for key, item in record.items()}
        for name, record in files.items()
        if isinstance(record, dict)
    }


def _progress_inventory(path: Path) -> dict[str, dict[str, str]]:
    return _prior_inventory(path)


def _completion_state(
    version_dir: Path, state_path: Path
) -> dict[str, dict[str, str]] | None:
    manifest = version_dir / _MANIFEST
    receipt = version_dir / _RECEIPT
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    manifest_record = value.get("manifest")
    receipt_record = value.get("receipt")
    if not isinstance(manifest_record, dict) or not isinstance(receipt_record, dict):
        return None
    if (
        manifest_record.get("sha256") != _sha256(manifest)
        or receipt_record.get("sha256") != _sha256(receipt)
    ):
        return None
    records: dict[str, dict[str, str]] = {}
    for name, record in (("manifest", manifest_record), ("receipt", receipt_record)):
        file_id = record.get("id")
        digest = record.get("sha256")
        if not isinstance(file_id, str) or not file_id or not isinstance(digest, str):
            return None
        records[name] = {
            "id": file_id,
            "sha256": digest,
            "webViewLink": str(record.get("webViewLink", "")),
        }
    return records


def _validate_index(path: Path) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublishBoundaryError(f"delta/INDEX.json is missing or malformed: {path}") from error
    if not isinstance(value, list):
        raise PublishBoundaryError(f"delta/INDEX.json must be a JSON array: {path}")
    required = {"source_key", "sha256", "collected_at", "sections"}
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != required:
            raise PublishBoundaryError(
                "delta/INDEX.json entries must contain source_key, sha256, collected_at, sections"
            )


def _route_check(path: Path, relative: str) -> None:
    payload = path.read_bytes().decode("utf-8", "replace")
    try:
        if relative == "delta/INDEX.json":
            _ = assert_route_allowed(
                payload,
                "drive",
                payload_kind="index",
                classification="owner-private",
            )
        else:
            _ = assert_route_allowed(payload, "drive", classification=classify(payload))
    except RouteRefused as error:
        raise PublishBoundaryError(f"Drive route refused {relative}: {error}") from error


def _remote_matches(
    transport: DriveTransport, record: dict[str, str] | None, local: Path
) -> bool:
    if not record or record.get("sha256") != _sha256(local) or not record.get("id"):
        return False
    try:
        transport.verify_owner_only(record["id"])
    except PublishPermissionError:
        raise
    except Exception as error:
        raise PublishPermissionError(str(error)) from error
    try:
        return transport.download_and_verify(record["id"], local) == record["sha256"]
    except PublishShaMismatch:
        raise
    except Exception as error:
        raise PublishShaMismatch(str(error)) from error


def _upload(
    transport: DriveTransport,
    version_dir: Path,
    slug: str,
    version: str,
    local: Path,
    prior: dict[str, str] | None,
) -> PublishedFile:
    relative = local.relative_to(version_dir).as_posix()
    _route_check(local, relative)
    folder_parts = (_root_folder(), "proposal", slug, version, *relative.split("/")[:-1])
    try:
        parent_id = transport.ensure_folder_path(folder_parts)
        result = transport.upsert_file(local, local.name, parent_id, (prior or {}).get("id"))
    except PublishError:
        raise
    except Exception as error:
        raise PublishError(f"Drive upload failed for {relative}: {error}") from error
    file_id = result.get("id", "")
    if not file_id:
        raise PublishError(f"Drive upload returned no id: {relative}")
    try:
        transport.verify_owner_only(file_id)
    except PublishPermissionError:
        raise
    except Exception as error:
        raise PublishPermissionError(f"owner-only verification failed: {relative}: {error}") from error
    try:
        digest = transport.download_and_verify(file_id, local)
    except PublishShaMismatch:
        raise
    except Exception as error:
        raise PublishShaMismatch(f"Drive read-back failed: {relative}: {error}") from error
    expected = _sha256(local)
    if digest != expected:
        raise PublishShaMismatch(f"Drive read-back sha256 mismatch: {relative}")
    return PublishedFile(
        relative,
        file_id,
        digest,
        result.get("webViewLink", ""),
        local.stat().st_size,
    )


def _record(uploaded: PublishedFile) -> dict[str, str]:
    return {
        "id": uploaded.id,
        "sha256": uploaded.sha256,
        "webViewLink": uploaded.web_view_link,
    }


def publish_version(
    root: Path,
    slug: str,
    version: str,
    *,
    transport: DriveTransport | None = None,
) -> PublishResult:
    """Traverse, upload, finalize manifest, and write/upload the receipt last."""
    version_dir = _version_directory(root, slug, version)
    tree_files = _walk_files(version_dir)
    index_path = version_dir / "delta" / "INDEX.json"
    if index_path.is_file():
        _validate_index(index_path)
    manifest_path = version_dir / _MANIFEST
    prior = _prior_inventory(manifest_path)
    base = _manifest_base(manifest_path)
    if base.get("draft_preview") is True:
        raise PublishDraftPreviewError(
            "manifest is a draft preview (draft_preview: true)"
        )
    state_path = root.expanduser().resolve() / slug / ".publish-state" / f"{version}.json"
    prior.update(_progress_inventory(state_path))
    completed = _completion_state(version_dir, state_path)
    # Owner-Drive review artifacts are ungated by docs/guide/drive-publish.md:19-27.
    # Safety is structural instead: every upload must remain owner-only and pass SHA read-back.
    # Sharing, permission changes, and notifications are external effects this skill does not offer.
    drive = transport or _transport_from_environment(root)
    uploads: list[PublishedFile] = []
    published: list[PublishedFile] = []

    for local in tree_files:
        relative = local.relative_to(version_dir).as_posix()
        previous = prior.get(relative)
        if _remote_matches(drive, previous, local):
            assert previous is not None
            published.append(
                PublishedFile(
                    relative,
                    previous["id"],
                    previous["sha256"],
                    previous.get("webViewLink", ""),
                    local.stat().st_size,
                )
            )
            continue
        uploaded = _upload(drive, version_dir, slug, version, local, previous)
        uploads.append(uploaded)
        published.append(uploaded)
        prior[relative] = _record(uploaded)
        _atomic_write(state_path, _json_bytes({"files": prior}))

    base["files"] = {item.path: _record(item) for item in published}
    _atomic_write(manifest_path, _json_bytes(base))
    manifest_prior = completed["manifest"] if completed is not None else None
    if completed is not None and _remote_matches(drive, manifest_prior, manifest_path):
        assert manifest_prior is not None
        manifest_file = PublishedFile(
            _MANIFEST,
            manifest_prior["id"],
            manifest_prior["sha256"],
            manifest_prior.get("webViewLink", ""),
            manifest_path.stat().st_size,
        )
    else:
        manifest_file = _upload(
            drive, version_dir, slug, version, manifest_path, manifest_prior
        )
        uploads.append(manifest_file)

    receipt_path = version_dir / _RECEIPT
    receipt_payload = {"manifest": {"id": manifest_file.id, "sha256": manifest_file.sha256}}
    _atomic_write(receipt_path, _json_bytes(receipt_payload))
    receipt_previous = completed["receipt"] if completed is not None else None
    if receipt_previous is not None and _remote_matches(drive, receipt_previous, receipt_path):
        receipt_file = PublishedFile(
            _RECEIPT,
            receipt_previous["id"],
            receipt_previous["sha256"],
            receipt_previous.get("webViewLink", ""),
            receipt_path.stat().st_size,
        )
    else:
        receipt_file = _upload(
            drive, version_dir, slug, version, receipt_path, receipt_previous
        )
        uploads.append(receipt_file)
    _atomic_write(
        state_path,
        _json_bytes(
            {
                "files": {item.path: _record(item) for item in published},
                "manifest": _record(manifest_file),
                "receipt": _record(receipt_file),
            }
        ),
    )

    return PublishResult(slug, version, tuple(published), tuple(uploads), receipt_path)


def _transport_from_environment(root: Path) -> DriveTransport:
    if os.environ.get("DRIVE_TRANSPORT") == "fake":
        return FakeDriveTransport(
            root / ".proposal-publish-fake-drive.json",
            os.environ.get("PROPOSAL_PUBLISH_FAKE_FAIL"),
        )
    from automation.drive_client import DriveClient

    cache = Path(
        os.environ.get(
            "PROPOSAL_PUBLISH_DRIVE_CACHE",
            str(Path.home() / ".hermes" / "proposal" / "drive-folders.json"),
        )
    ).expanduser()
    return DriveClient(os.environ.get("DRIVE_GWS_BIN", "gws"), cache)


def command(args: argparse.Namespace) -> int:
    try:
        result = publish_version(
            Path(os.environ.get("PROPOSAL_ROOT", "~/proposals")),
            args.slug,
            args.version,
        )
    except PublishError as error:
        print(f"PROPOSAL-PUBLISH-REFUSED {error}", file=sys.stderr)
        return error.exit_code
    payload = {
        "files": [
            {
                "path": item.path,
                "id": item.id,
                "sha256": item.sha256,
                "webViewLink": item.web_view_link,
                "delivery": "drive-link",
                "over_attachment_limit": item.size > _DRIVE_LINK_THRESHOLD,
            }
            for item in result.files
        ],
        "receipt": str(result.receipt),
        "slug": result.slug,
        "uploads": [item.path for item in result.uploads],
        "version": result.version,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"PROPOSAL-PUBLISHED slug={result.slug} version={result.version}")
        for item in result.files:
            print(f"UPLOAD path={item.path} id={item.id} link={item.web_view_link}")
        print(f"RECEIPT path={result.receipt}")
    return 0
