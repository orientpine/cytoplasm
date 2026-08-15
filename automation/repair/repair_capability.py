"""Race-safe HMAC capability binding for repair reports.

Verification authenticates only ``ticket_id`` and ``occurrence``. It does not
authenticate a report's operation or reason; consumers must treat those fields
as untrusted input.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import time
from fcntl import LOCK_EX, flock
from pathlib import Path
from typing import Final, TypedDict


_KEY_NAME: Final = "repair-report-capability.key"
_LOCK_NAME: Final = "repair-capability.lock"
_OCCURRENCE: Final = re.compile(r"^[0-9]{1,9}$")
_RECORD_KEYS: Final = frozenset({"ticket_id", "occurrence", "mac", "issued_at"})


class CapabilityKeyError(RuntimeError):
    """Raised when an existing capability key is unsafe or malformed."""


class InvalidOccurrenceError(ValueError):
    """Raised when a repair occurrence cannot be represented canonically."""


class CapabilityRegistryError(RuntimeError):
    """Raised internally when the read-only repair registry snapshot is malformed."""


class PublishedCapability(TypedDict):
    """Serialized capability record shared with the ops reader."""

    ticket_id: str
    occurrence: str
    mac: str
    issued_at: str


def _lock_path() -> Path:
    return Path.home() / ".hermes" / _LOCK_NAME


def _write_all(file_descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(file_descriptor, payload[written:])
        if count == 0:
            raise OSError("atomic file write made no progress")
        written += count


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _secret_locked() -> bytes:
    """Load or atomically create the key while the caller holds the lock."""
    key_path = _lock_path().with_name(_KEY_NAME)
    try:
        metadata = key_path.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if not stat.S_ISREG(metadata.st_mode):
            raise CapabilityKeyError("capability key is not a regular file")
        if metadata.st_size != 32:
            raise CapabilityKeyError("capability key has an invalid length")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise CapabilityKeyError("capability key has an invalid mode")
        descriptor = os.open(key_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            current = os.fstat(descriptor)
            key = os.read(descriptor, 33)
        finally:
            os.close(descriptor)
        if not stat.S_ISREG(current.st_mode) or current.st_size != 32 or len(key) != 32:
            raise CapabilityKeyError("capability key changed while being read")
        if stat.S_IMODE(current.st_mode) != 0o600:
            raise CapabilityKeyError("capability key mode changed while being read")
        return key

    key = secrets.token_bytes(32)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{_KEY_NAME}.", dir=key_path.parent)
    temporary_path = Path(temporary_name)
    try:
        _write_all(descriptor, key)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, key_path)
        _fsync_directory(key_path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _unlink_if_present(temporary_path)
    return key


def secret() -> bytes:
    """Return the stable local capability key under one exclusive lock."""
    lock_path = _lock_path()
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path.parent.chmod(0o700)
    with lock_path.open("a+b") as lock_handle:
        flock(lock_handle.fileno(), LOCK_EX)
        return _secret_locked()


def _canon(occurrence: int | str) -> str:
    try:
        canonical = str(int(occurrence))
    except (TypeError, ValueError, OverflowError) as error:
        raise InvalidOccurrenceError("repair occurrence is not an integer") from error
    if canonical.startswith("-") or len(canonical) > 9:
        raise InvalidOccurrenceError("repair occurrence is outside the supported range")
    return canonical


def mac(ticket_id: str, occurrence: int | str) -> str:
    canonical = _canon(occurrence)
    payload = f"{ticket_id}|{canonical}".encode()
    return hmac.new(secret(), payload, hashlib.sha256).hexdigest()


def capability_dir() -> Path:
    return Path(os.environ.get("REPAIR_CAPABILITY_DIR", "/srv/autophagy-repair-capability"))


def registry_path() -> Path:
    return Path(os.environ.get("REPAIR_STATE_FILE", "~/.hermes/repair-tickets.json")).expanduser()


def _publish_locked(ticket_id: str, occurrence: str, key: bytes) -> None:
    """Publish a monotonic record while the caller holds the capability lock."""
    directory = capability_dir()
    destination = directory / f"{ticket_id}.json"
    try:
        current_payload = json.loads(destination.read_text(encoding="utf-8"))
    except FileNotFoundError:
        current_payload = None
    if current_payload is not None and int(current_payload["occurrence"]) >= int(occurrence):
        return

    payload = f"{ticket_id}|{occurrence}".encode()
    record: PublishedCapability = {
        "ticket_id": ticket_id,
        "occurrence": occurrence,
        "mac": hmac.new(key, payload, hashlib.sha256).hexdigest(),
        "issued_at": str(int(time.time())),
    }
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{ticket_id}.", dir=directory)
    temporary_path = Path(temporary_name)
    try:
        _write_all(descriptor, encoded)
        os.fchmod(descriptor, 0o640)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, destination)
        _fsync_directory(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _unlink_if_present(temporary_path)


def publish(ticket_id: str, occurrence: int | str) -> None:
    """Publish a capability if its pre-created destination directory is usable."""
    directory = capability_dir()
    if not directory.is_dir():
        print("repair capability publish skipped: directory unavailable", file=sys.stderr)
        return
    try:
        lock_path = _lock_path()
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path.parent.chmod(0o700)
        with lock_path.open("a+b") as lock_handle:
            flock(lock_handle.fileno(), LOCK_EX)
            canonical = _canon(occurrence)
            key = _secret_locked()
            _publish_locked(ticket_id, canonical, key)
    except OSError:
        print("repair capability publish skipped: filesystem unavailable", file=sys.stderr)


def read_published(ticket_id: str) -> dict[str, str] | None:
    """Return one valid published record, treating damage as absence."""
    try:
        payload = json.loads((capability_dir() / f"{ticket_id}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != _RECORD_KEYS:
        return None
    if not all(isinstance(value, str) for value in payload.values()):
        return None
    occurrence = payload["occurrence"]
    if _OCCURRENCE.fullmatch(occurrence) is None or str(int(occurrence)) != occurrence:
        return None
    return {key: payload[key] for key in _RECORD_KEYS}


def reconcile_capabilities(*, limit: int = 200) -> int:
    """Publish missing or stale capabilities from a read-only registry snapshot."""
    try:
        payload = json.loads(registry_path().read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CapabilityRegistryError("repair registry is not an object")
        entries: list[tuple[str, int]] = []
        for entry in payload.values():
            if not isinstance(entry, dict):
                raise CapabilityRegistryError("repair registry entry is not an object")
            ticket_id = entry.get("ticket_id")
            occurrences = entry.get("occurrences")
            if not isinstance(ticket_id, str) or not isinstance(occurrences, int) or occurrences < 1:
                raise CapabilityRegistryError("repair registry entry fields are malformed")
            entries.append((ticket_id, occurrences))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, CapabilityRegistryError):
        print("repair capability reconcile skipped: registry unavailable", file=sys.stderr)
        return 0

    if limit <= 0 or not capability_dir().is_dir():
        return 0
    published = 0
    for ticket_id, occurrences in entries:
        current = read_published(ticket_id)
        if current is not None and int(current["occurrence"]) >= occurrences:
            continue
        publish(ticket_id, occurrences)
        published += 1
        if published >= limit:
            break
    return published


def verify(ticket_id: str, occurrence: int | str, mac_value: str) -> bool:
    canonical = _canon(occurrence)
    expected = hmac.new(secret(), f"{ticket_id}|{canonical}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, mac_value)
