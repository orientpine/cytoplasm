#!/usr/bin/env python3
"""Idempotent, exact-preimage patch: withhold approval details on public Discord.

Companion to ``patch_public_message_policy.py`` (root ticket t_db6a60e8). The
gateway seam covers the turn-driven approval prompt, but ``send_exec_approval``
is a public adapter method: plugins and recovery paths can call it directly with
whatever ``metadata`` they happen to hold. Enforcing the rule a second time *at
the adapter* means the raw command cannot reach a guild channel through a caller
the gateway seam never sees.

The adapter deliberately does not import the carrier policy module. At this
point it already holds the resolved ``channel`` object, so ``channel.guild`` is
stronger evidence than any ``chat_type`` string the caller passed: a Discord DM
channel has ``guild is None``, while a guild channel *and a thread inside one*
both have a guild. Missing/unknown metadata therefore resolves to "public"
(fail-closed) without a cross-module dependency on a code path that must keep
working even when the carrier is half-deployed.

Preimage taken from the vendored source running on the node (Hermes v0.20.3,
head ``a3995f8a``), verified identical on the agent and peer installs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

MARKER: Final = "_hermes_pubapproval_done"
BACKUP_SUFFIX: Final = ".autophagy-orig"

_MOD1_PRE: Final = (
    "            channel = self._client.get_channel(int(target_id))\n"
    "            if not channel:\n"
    "                channel = await self._client.fetch_channel(int(target_id))\n"
    "\n"
    "            # Keep the approval request self-contained in plain message content.\n"
)
_MOD1_POST: Final = (
    "            channel = self._client.get_channel(int(target_id))\n"
    "            if not channel:\n"
    "                channel = await self._client.fetch_channel(int(target_id))\n"
    "\n"
    "            # autophagy hermes_compat (_hermes_pubapproval_done): the approval request\n"
    "            # stays public so the owner can act on it, but the requested command and\n"
    "            # the reason are internal tool arguments. The gateway applies the same rule\n"
    "            # for turn-driven approvals; this covers direct/plugin callers.\n"
    "            # A DM channel has guild None; a guild channel and a thread inside one do\n"
    "            # not — so anything we cannot prove to be a DM is treated as public.\n"
    "            _pubapproval_chat_type = str((metadata or {}).get(\"chat_type\") or \"\").strip().lower()\n"
    "            if _pubapproval_chat_type:\n"
    '                _pubapproval_public = _pubapproval_chat_type != "dm"\n'
    "            else:\n"
    '                _pubapproval_public = getattr(channel, "guild", None) is not None\n'
    "            if _pubapproval_public:\n"
    "                logger.info(\n"
    '                    "Public Discord delivery suppressed: event=%s chat_id=%s thread_id=%s",\n'
    '                    "approval_request_details",\n'
    "                    str(chat_id)[:96],\n"
    '                    str((metadata or {}).get("thread_id") or "")[:96],\n'
    "                )\n"
    '                command = "[operation details withheld on public Discord]"\n'
    '                description = "A protected operation requires your approval."\n'
    "\n"
    "            # Keep the approval request self-contained in plain message content.\n"
)

_MODS: Final = (("MOD1", _MOD1_PRE, _MOD1_POST),)


class PreimageError(RuntimeError):
    """Raised when target source differs from an expected exact preimage."""


def is_patched(src: str) -> bool:
    """Return True only when the approval-withholding modification is present."""
    return MARKER in src and all(post in src for _, _, post in _MODS)


def patch_source(src: str, *, filename: str = "<adapter.py>") -> str:
    """Return a compile-gated patch or raise PreimageError on source drift."""
    if is_patched(src):
        return src
    for name, preimage, _ in _MODS:
        count = src.count(preimage)
        if count != 1:
            raise PreimageError(
                f"{name} preimage found {count} times (expected exactly 1); refusing to patch"
            )
    patched = src
    for _, preimage, postimage in _MODS:
        patched = patched.replace(preimage, postimage, 1)
    _ = compile(patched, filename, "exec")
    if not is_patched(patched):
        raise PreimageError("post-patch verification failed")
    return patched


def verify_source(src: str) -> bool:
    """Return True when the adapter-side approval withholding is installed."""
    return is_patched(src) and all(
        needle in src
        for needle in (
            "_pubapproval_chat_type = str((metadata or {}).get(",
            'command = "[operation details withheld on public Discord]"',
            'description = "A protected operation requires your approval."',
        )
    )


def _apply(path: Path) -> int:
    original = path.read_text(encoding="utf-8")
    if is_patched(original):
        print(f"ALREADY-PATCHED {path}")
        return 0
    patched = patch_source(original, filename=str(path))
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        _ = backup.write_text(original, encoding="utf-8")
    temporary = path.with_name(path.name + ".autophagy-tmp")
    try:
        _ = temporary.write_text(patched, encoding="utf-8")
        _ = temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f"PATCHED {path} (backup: {backup})")
    return 0


def _verify(path: Path) -> int:
    if verify_source(path.read_text(encoding="utf-8")):
        print(f"VERIFIED {path}")
        return 0
    print(f"NOT-PATCHED {path}", file=sys.stderr)
    return 3


def _revert(path: Path) -> int:
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        print(f"NO-BACKUP {backup}", file=sys.stderr)
        return 3
    _ = path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"REVERTED {path} (from {backup})")
    return 0


def main(argv: tuple[str, ...]) -> int:
    """Execute apply, verify, or revert against one vendored discord adapter.py."""
    match argv:
        case ("apply", target):
            return _apply(Path(target))
        case ("verify", target):
            return _verify(Path(target))
        case ("revert", target):
            return _revert(Path(target))
        case _:
            print(
                "usage: patch_discord_public_approval.py apply|verify|revert <adapter.py>",
                file=sys.stderr,
            )
            return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(tuple(sys.argv[1:])))
    except PreimageError as exc:
        print(f"PREIMAGE-ERROR {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
