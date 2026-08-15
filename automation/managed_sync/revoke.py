"""Managed-skill revocation flow (MS-S5): activation block + owner-gated request.

Revocation NEVER detaches a live skill automatically (SI-7). This module is
strictly read-only: it inspects live entries via readlink and, when the
activated digest is revoked, emits a ``RemovalRequest`` whose text names the
exact owner-gated command. The owner alone runs that command through the
existing deploy gate; nothing here mutates the live store.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from .pipeline import RemovalRequest
from .state import ManagedSyncState

DEFAULT_LIVE_ROOT: Final = Path("/srv/autophagy-skills/live")

_RELEASE_DIGEST: Final = re.compile(r"[0-9a-f]{64}\Z")
_REMOVE_COMMAND: Final = "automation/deploy-skill.sh {skill} --remove"
_LOGGER: Final = logging.getLogger(__name__)


class LiveStateError(Exception):
    """The authoritative live entry cannot be interpreted safely."""


def live_activated_digest(link: Path) -> str | None:
    """Return the digest from a valid live link, or None when the entry is absent."""
    if not link.is_symlink():
        if link.exists():
            raise LiveStateError(f"live entry is not a symlink: {link}")
        return None
    try:
        digest = link.readlink().name
    except OSError as error:
        raise LiveStateError(f"live entry cannot be read: {link}") from error
    if _RELEASE_DIGEST.fullmatch(digest) is None:
        raise LiveStateError(f"live entry has an invalid release digest {digest!r}: {link}")
    if not link.exists():
        raise LiveStateError(f"live entry is a dangling symlink: {link}")
    return digest


def _activated_digest(link: Path) -> str | None:
    """Read one live entry's activated digest, or None when nothing is live.

    Missing entry, dangling target, and a non-symlink entry all yield None
    (idempotent: nothing is activated, so nothing is requested). A target
    whose last path component is not a release digest is ambiguous, so it
    warns and yields None (fail-closed: request nothing).
    """
    try:
        return live_activated_digest(link)
    except LiveStateError as error:
        _LOGGER.warning("live entry %s cannot be read (%s); requesting nothing", link, error)
        return None


def check_live(
    state: ManagedSyncState,
    opted_in_skills: Iterable[str],
    *,
    live_root: Path = DEFAULT_LIVE_ROOT,
) -> tuple[RemovalRequest, ...]:
    """Emit one owner-gated RemovalRequest per live skill whose digest is revoked.

    Read-only over ``live_root``: the activated digest is resolved from the
    live symlink target's last path component and compared against the skill's
    revoked set. Everything else — no symlink, dangling target, non-revoked or
    unparseable digest — emits nothing.
    """
    requests: list[RemovalRequest] = []
    for skill in opted_in_skills:
        digest = _activated_digest(live_root / skill)
        if digest is None or digest not in state.skill(skill).revoked_digests:
            continue
        command = _REMOVE_COMMAND.format(skill=skill)
        requests.append(
            RemovalRequest(
                skill=skill,
                digest=digest,
                reason=f"activated digest {digest} is revoked; owner must run `{command}`",
            )
        )
    return tuple(requests)


def render_removal_instruction(request: RemovalRequest) -> str:
    """Render the owner-facing instruction for one removal request."""
    command = _REMOVE_COMMAND.format(skill=request.skill)
    return (
        f"[managed-skill] 회수(revocation) 감지: `{request.skill}`의 활성 digest가 회수되었습니다.\n"
        f"- digest: {request.digest}\n"
        f"- reason: {request.reason}\n"
        f"- 자동 해제는 하지 않습니다(SI-7). 소유자 확인 후 직접 실행: `{command}`"
    )
