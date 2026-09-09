"""Byte-exact retirement of an executed release approval."""
from __future__ import annotations

import json
import os
import tempfile
import sys
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from automation import owner_notice, skill_gate
from automation.interop.approval_types import Probe
from automation.release_spec import ReleaseSpecError
from automation.skill_gate_request import lease


def archive_bytes(archive_root: Path, name: str, encoded: bytes) -> Path:
    """Move one approval record's exact bytes into a 0700 archive as a 0600 file.

    Single copy on purpose: the executed-release archive (``retire_released_record``)
    and the audited abandon (``release_abandon``) must write history identically —
    two archivers drift apart exactly where one of them stops being byte-exact.
    """
    archive_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive_root.chmod(0o700)
    target = archive_root / name
    if target.exists():
        if target.read_bytes() != encoded:
            raise ReleaseSpecError("release history conflicts with pending bytes")
        return target
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=archive_root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def notify_stale_approval(record: Mapping[str, str], tip: str) -> None:
    """One owner notice per request identity, even while the destination tip advances."""
    identity = json.dumps([record["version"], record["head_sha"], record["message_id"]])
    key = sha256(identity.encode()).hexdigest()
    root = skill_gate.GATE_DIR / "release-stale-notified"
    try:
        with lease(skill_gate.GATE_DIR).hold("release-stale-notice") as owned:
            if not owned or (root / key).exists():
                return
            body = (
                f"⛔ 릴리스 {record['version']} 승인 기준 {record['head_sha'][:12]}가 "
                f"origin/main {tip[:12]}와 달라 자동 완결할 수 없습니다. "
                "automation/release.sh를 실행하면 옛 요청을 감사 회수하고 새 승인을 요청합니다."
            )
            if owner_notice.notify_owner(body):
                archive_bytes(root, key, b"notified\n")
                print(f"RELEASE-STALE-NOTIFIED episode={key[:12]}", file=sys.stderr)
            else:
                print(f"RELEASE-STALE-NOTIFY-FAIL episode={key[:12]}", file=sys.stderr)
    except (OSError, ReleaseSpecError) as error:
        print(f"RELEASE-STALE-NOTIFY-FAIL {type(error).__name__}", file=sys.stderr)


def retire_released_record(
    record_path: Path,
    archive_root: Path,
    *,
    expected_head: str,
    decision: Probe,
) -> Path | None:
    """Archive one executed release approval before the next request exists."""
    try:
        encoded = record_path.read_bytes()
        decoded = json.loads(encoded)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseSpecError("release record is unreadable") from error
    if not isinstance(decoded, dict):
        raise ReleaseSpecError("release record is not an object")
    head = str(decoded.get("head_sha", ""))
    if head != expected_head or len(head) != 40:
        raise ReleaseSpecError("pending release does not match the latest signed head")
    if decision is not Probe.APPROVED:
        raise ReleaseSpecError("only an approved release can leave pending")
    target = archive_bytes(archive_root, f"{head}.json", encoded)
    record_path.unlink()
    return target
