from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Iterator

from .patent_storage import require_slug


# Mirrors approval_surface.ApprovalKind.PATENT_EXPORT, which a deployed skill cannot
# import here. Pinned against drift by tests/unit/test_patent_export_binding.py.
_KIND: Final = "patent-export"


class ManifestError(RuntimeError):
    """Manifest validation or concurrency error."""


def _export_root() -> Path:
    path = Path(os.environ.get("PATENT_EXPORT_ROOT", "~/.hermes/patent-export")).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


class State(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CANCELLED = "CANCELLED"
    CONSUMED = "CONSUMED"


@dataclass(frozen=True, slots=True)
class Manifest:
    slug: str
    plaintext_sha256: str
    dest_folder_id: str
    mode: str
    expiry_ts: int
    nonce: str
    state: State
    message_id: str | None
    created_ts: int
    approval_ts: int | None
    #: Thread this request's approval message lives in. Outside every field the gate
    #: binds (sha256/dest/mode/expiry), so adding it cannot change a stored decision;
    #: it sits before the binding so the record still ENDS in the whole binding.
    approval_thread_id: str | None
    kind: str
    surface: str | None
    channel_id: str | None
    policy_version: int

    @property
    def is_bound(self) -> bool:
        """True once the record itself names the surface its message was posted to."""
        return self.surface is not None and self.channel_id is not None


def manifest_path(slug: str) -> Path:
    return _export_root() / f"{require_slug(slug)}.json"


def write_manifest(m: Manifest) -> None:
    path = manifest_path(m.slug)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = {
        "slug": m.slug,
        "plaintext_sha256": m.plaintext_sha256,
        "dest_folder_id": m.dest_folder_id,
        "mode": m.mode,
        "expiry_ts": m.expiry_ts,
        "nonce": m.nonce,
        "state": m.state.value,
        "message_id": m.message_id,
        "created_ts": m.created_ts,
        "approval_ts": m.approval_ts,
        "approval_thread_id": m.approval_thread_id,
        "kind": m.kind,
        "surface": m.surface,
        "channel_id": m.channel_id,
        "policy_version": m.policy_version,
    }
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)


_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX = re.compile(r"^[0-9a-f]{8,}$")


def _valid_sha(value: object) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError("invalid sha256")
    return value


def _valid_mode(value: object) -> str:
    if value not in ("enc", "plaintext"):
        raise ValueError("invalid mode")
    return str(value)


def _valid_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("invalid int")
    return value


def _valid_hex(value: object) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise ValueError("invalid nonce")
    return value


def _valid_str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid string")
    return value


def _valid_opt_str(value: object) -> str | None:
    return None if value is None else _valid_str(value)


def _valid_opt_int(value: object) -> int | None:
    return None if value is None else _valid_int(value)


def load_manifest(slug: str) -> Manifest:
    path = manifest_path(slug)
    if not path.exists():
        raise ManifestError(f"Manifest not found for {slug}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"Invalid JSON in manifest for {slug}") from e
    if not isinstance(payload, dict):
        raise ManifestError("Manifest is not a dict")
    try:
        manifest = Manifest(
            slug=require_slug(payload["slug"]),
            plaintext_sha256=_valid_sha(payload["plaintext_sha256"]),
            dest_folder_id=_valid_str(payload["dest_folder_id"]),
            mode=_valid_mode(payload["mode"]),
            expiry_ts=_valid_int(payload["expiry_ts"]),
            nonce=_valid_hex(payload["nonce"]),
            state=State(payload["state"]),
            message_id=_valid_opt_str(payload.get("message_id")),
            created_ts=_valid_int(payload["created_ts"]),
            approval_ts=_valid_opt_int(payload.get("approval_ts")),
            approval_thread_id=_valid_opt_str(payload.get("approval_thread_id")),
            kind=_valid_str(payload.get("kind", _KIND)),
            surface=_valid_opt_str(payload.get("surface")),
            channel_id=_valid_opt_str(payload.get("channel_id")),
            policy_version=_valid_int(payload.get("policy_version", 0)),
        )
    except (KeyError, ValueError, TypeError) as e:
        raise ManifestError(f"Invalid manifest structure for {slug}") from e
    if manifest.slug != require_slug(slug):
        raise ManifestError("manifest slug does not match its path")
    return manifest


@contextmanager
def lock(slug: str) -> Iterator[None]:
    lock_path = _export_root() / f"{require_slug(slug)}.lock"
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except BlockingIOError:
        raise ManifestError(f"Export already in progress for {slug}") from None
    finally:
        os.close(fd)


def transition(slug: str, *, allowed_from: frozenset[State], to: State, **fields) -> Manifest:
    m = load_manifest(slug)
    if m.state not in allowed_from:
        raise ManifestError(f"Cannot transition from {m.state} to {to}")
    
    new_fields = {
        "slug": m.slug,
        "plaintext_sha256": m.plaintext_sha256,
        "dest_folder_id": m.dest_folder_id,
        "mode": m.mode,
        "expiry_ts": m.expiry_ts,
        "nonce": m.nonce,
        "state": to,
        "message_id": m.message_id,
        "created_ts": m.created_ts,
        "approval_ts": m.approval_ts,
        "approval_thread_id": m.approval_thread_id,
        "kind": m.kind,
        "surface": m.surface,
        "channel_id": m.channel_id,
        "policy_version": m.policy_version,
    }
    new_fields.update(fields)
    
    new_m = Manifest(**new_fields)
    write_manifest(new_m)
    return new_m


def archive_folder_id() -> str:
    folder_id = os.environ.get("PATENT_ARCHIVE_FOLDER_ID", "")
    if not folder_id:
        config_path = _export_root() / "config.json"
        if config_path.exists():
            try:
                folder_id = json.loads(config_path.read_text(encoding="utf-8")).get("archive_folder_id", "")
            except (json.JSONDecodeError, OSError):
                pass
    
    if not folder_id or folder_id == "none" or (folder_id.startswith("<") and folder_id.endswith(">")):
        raise ManifestError("Invalid or missing archive_folder_id")
    
    return folder_id


def mint_nonce() -> str:
    return secrets.token_hex(16)


def now_ts() -> int:
    return int(time.time())


def list_active() -> tuple[Manifest, ...]:
    """Return manifests still awaiting or holding approval (PENDING or APPROVED)."""
    root = _export_root()
    out: list[Manifest] = []
    for path in sorted(root.glob("*.json")):
        if path.name == "config.json":
            continue
        try:
            m = load_manifest(path.stem)
        except ManifestError:
            continue
        if m.state in {State.PENDING, State.APPROVED}:
            out.append(m)
    return tuple(out)
