"""Hash-bound single-post ledger for managed-skill release announcements.

This is AGENTS.md 「승인 메시지 단일성 규칙」 applied to a message that is not an
approval. An announce carries no ✅/⛔ decision, so it cannot route through
``approval_lifecycle.request_owner_approval`` — that façade's whole job is to
probe an owner decision that does not exist here. What IS reused is the
technique, and the primitives it is built from:

L1  All mutation happens while this process holds the key's lease
    (``FileKeyLease``, shared with every approval producer).
L2  A stored message id is never replaced. An announce is history, not a
    pending request, so there is no supersede path at all — a second publish of
    the same release is collapsed into the record that already exists.
L4  A record whose binding does not match is refused, never overwritten.

The action hash binds the announce to the release CONTENT (manifest digest,
tag, channel), so re-running publish for an unchanged release is a no-op while
a changed binding is surfaced instead of silently double-posted.

State lives outside the checkout at ``~/.hermes/managed-skills/announce``
(override: ``MANAGED_ANNOUNCE_STATE_DIR``) per the repo's 「추적 config = 불변
시드」 rule.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.interop.approval_lease import FileKeyLease, PostingJournal, slug

STATE_DIR_ENV: Final = "MANAGED_ANNOUNCE_STATE_DIR"
DEFAULT_STATE_DIR: Final = Path("~/.hermes/managed-skills/announce")

_RECORD_KEYS: Final = ("key", "action_hash", "message_id", "channel_id", "created_at")


class AnnounceLedgerError(Exception):
    """The announce ledger cannot be read or written; nothing may be posted."""


@dataclass(frozen=True, slots=True)
class AnnounceRecord:
    """Proof that one release was announced exactly once, into one channel."""

    key: str
    action_hash: str
    message_id: str
    channel_id: str
    created_at: str


def state_dir() -> Path:
    """Resolve the announce ledger root from the environment or runtime default."""
    raw = os.environ.get(STATE_DIR_ENV, "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_STATE_DIR.expanduser()


def announce_key(skill: str, tag: str) -> str:
    """One logical announce request — a release is announced once, forever."""
    return f"managed-announce:{skill}:{tag}"


def announce_action_hash(*, manifest_digest: str, tag: str, channel_id: str) -> str:
    """Bind the announce to the release content and the channel it targets."""
    preimage = json.dumps(
        {"channel_id": channel_id, "manifest_digest": manifest_digest, "tag": tag},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(preimage.encode('utf-8')).hexdigest()}"


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class AnnounceLedger:
    """Durable announce records plus the lease and journal that guard them."""

    root: Path

    @property
    def lease(self) -> FileKeyLease:
        return FileKeyLease(self.root)

    @property
    def journal(self) -> PostingJournal:
        return PostingJournal(self.root)

    def _path(self, key: str) -> Path:
        return self.root / f"{slug(key)}.announce.json"

    def read(self, key: str) -> AnnounceRecord | None:
        """Return the stored announce for ``key``; an unreadable record is fatal."""
        try:
            raw = self._path(key).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise AnnounceLedgerError(f"cannot read announce record: {key}") from error
        try:
            payload: object = json.loads(raw)
        except json.JSONDecodeError as error:
            raise AnnounceLedgerError(f"announce record is not valid JSON: {key}") from error
        if not isinstance(payload, dict):
            raise AnnounceLedgerError(f"announce record is not a JSON object: {key}")
        fields: dict[str, object] = {str(name): item for name, item in payload.items()}
        values: dict[str, str] = {}
        for field in _RECORD_KEYS:
            value = fields.get(field)
            if not isinstance(value, str) or not value:
                raise AnnounceLedgerError(f"announce record field is unusable: {key}.{field}")
            values[field] = value
        return AnnounceRecord(**values)

    def commit(self, record: AnnounceRecord) -> None:
        """Store one announce. An existing message id is NEVER replaced (L2)."""
        if self.read(record.key) is not None:
            raise AnnounceLedgerError(f"announce record already exists: {record.key}")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._path(record.key)
        _ = path.write_text(
            json.dumps(
                {field: getattr(record, field) for field in _RECORD_KEYS},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
