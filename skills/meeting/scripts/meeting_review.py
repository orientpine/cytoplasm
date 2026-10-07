"""Root-issued, exact-material meeting classification review (not an LLM routing override)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Not configurable by the ingesting agent or by a tracked repository file.
RECEIPT_DIR = Path("/etc/autophagy/meeting-reviews")
_MAX_RECEIPT_BYTES = 4096
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _trusted(path: Path, fd: int) -> bool:
    """Check each directory, including the trust anchor, and the opened inode."""
    try:
        if not path.is_absolute() or path.is_symlink():
            return False
        current = path.parent
        while True:
            info = current.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                return False
            if current == current.parent:
                break
            current = current.parent
        info = os.fstat(fd)
        return (stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and not info.st_mode & 0o022 and info.st_size <= _MAX_RECEIPT_BYTES)
    except OSError:
        return False


def approved(gate_input: str) -> bool:
    """Only a root-issued receipt for these exact gate bytes can lower downstream sensitivity.

    Absence, malformed JSON, untrusted paths and I/O errors retain the keyword verdict.
    Receipt strings must never enter logs, notifications or the generated note.
    """
    digest = hashlib.sha256(gate_input.encode("utf-8")).hexdigest()
    path = RECEIPT_DIR / f"{digest}.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if not _trusted(path, fd):
                return False
            with os.fdopen(fd, "rb", closefd=False) as handle:
                raw = handle.read(_MAX_RECEIPT_BYTES + 1)
            if len(raw) > _MAX_RECEIPT_BYTES:
                return False
        finally:
            os.close(fd)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or set(data) != {
            "version", "gate_sha256", "decision", "owner", "reason", "reviewed_on",
            "publication_scope",
        }:
            return False
        if (type(data["version"]) is not int or data["version"] != 1
                or data["decision"] != "non-sensitive"
                or data["publication_scope"] != "meeting-downstream"
                or not isinstance(data["gate_sha256"], str)
                or not _SHA256.fullmatch(data["gate_sha256"])
                or data["gate_sha256"] != digest):
            return False
        if any(not isinstance(data[key], str) or not data[key].strip()
               or len(data[key]) > 500 for key in ("owner", "reason")):
            return False
        if not isinstance(data["reviewed_on"], str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", data["reviewed_on"]
        ):
            return False
        return date.fromisoformat(data["reviewed_on"]) <= datetime.now(ZoneInfo("Asia/Seoul")).date()
    except (OSError, ValueError, UnicodeError, TypeError):
        return False
