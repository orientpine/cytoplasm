#!/usr/bin/env python3
"""Idempotent, exact-preimage patch for the vendored Hermes gateway.

Problem it fixes
----------------
``pre_gateway_dispatch`` is Hermes' only per-message plugin hook, but it is
invoked in exactly one place — ``_handle_message`` (gateway/run.py). When a
session is already busy (a turn is running) an arriving owner message is NOT
routed through ``_handle_message``: it is queued and later consumed by a
recursive ``_run_agent`` continuation that never invokes the hook. As a result:

* ``05-skill-generation`` never OBSERVES owner messages that land mid-turn, and
* ``00-meeting-gate`` never gets to VETO (fail-closed security) a meeting
  document uploaded mid-turn — the confidential body can enter the agent.

This module patches ``gateway/run.py`` to invoke ``pre_gateway_dispatch`` exactly
once at busy-entry too (top of ``_handle_active_session_busy_message``), guarded
by an ``event.metadata`` marker so the rare drain→``_handle_message`` path does
not double-fire. The inserted block is fail-safe: any error falls through to
normal handling; only an explicit ``skip`` drops the message.

Design invariants
-----------------
* **Exact preimage only** — never fuzzy-apply. If the expected source is not
  found byte-for-byte, refuse (exit 3) and change nothing.
* **Idempotent** — re-running is a no-op once the marker is present.
* **Reversible** — the original file is backed up to ``<file>.autophagy-orig``.
* **Syntax-checked** — the patched text must ``compile`` before it is written.

Upstream note: Hermes documents ``pre_gateway_dispatch`` as "Fired once per
incoming MessageEvent", so this is an upstream bug; carry this patch until an
upstream release restores that contract for the busy path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

MARKER: Final = "_hermes_pgd_done"
BACKUP_SUFFIX: Final = ".autophagy-orig"

# --- Modification 1: guard the existing idle-path hook block so it does not
# re-fire for a message already observed at busy-entry, and stamp the marker.
_MOD1_PRE: Final = (
    "        if not is_internal:\n"
    "            try:\n"
    "                from hermes_cli.plugins import invoke_hook as _invoke_hook\n"
    "                _hook_results = _invoke_hook(\n"
)
_MOD1_POST: Final = (
    '        if not is_internal and not event.metadata.get("_hermes_pgd_done"):\n'
    '            event.metadata["_hermes_pgd_done"] = True\n'
    "            try:\n"
    "                from hermes_cli.plugins import invoke_hook as _invoke_hook\n"
    "                _hook_results = _invoke_hook(\n"
)

# --- Modification 2: fire the hook once at the top of the busy-message handler.
_MOD2_ANCHOR: Final = (
    "    async def _handle_active_session_busy_message"
    "(self, event: MessageEvent, session_key: str) -> bool:\n"
)
_MOD2_INSERT: Final = (
    "        # autophagy hermes_compat (busy-path pre_gateway_dispatch):\n"
    "        # Fire pre_gateway_dispatch exactly once for messages that arrive\n"
    "        # while a turn is active, so observation plugins (skill-generation)\n"
    "        # and fail-closed veto plugins (meeting-gate) are not bypassed by the\n"
    "        # busy/continuation path (recursive _run_agent never invokes it).\n"
    "        # Idempotent via event.metadata marker; fail-safe: only an explicit\n"
    "        # skip drops, any error falls through to normal handling.\n"
    '        if not getattr(event, "internal", False) and not event.metadata.get("_hermes_pgd_done"):\n'
    '            event.metadata["_hermes_pgd_done"] = True\n'
    "            try:\n"
    "                from hermes_cli.plugins import invoke_hook as _pgd_invoke_hook\n"
    "                _pgd_results = _pgd_invoke_hook(\n"
    '                    "pre_gateway_dispatch",\n'
    "                    event=event,\n"
    "                    gateway=self,\n"
    '                    session_store=getattr(self, "session_store", None),\n'
    "                )\n"
    "            except Exception:\n"
    "                _pgd_results = []\n"
    "            for _pgd in _pgd_results or []:\n"
    "                if not isinstance(_pgd, dict):\n"
    "                    continue\n"
    '                _pgd_action = _pgd.get("action")\n'
    '                if _pgd_action == "skip":\n'
    "                    logger.info(\n"
    '                        "pre_gateway_dispatch skip (busy path): reason=%s",\n'
    '                        _pgd.get("reason"),\n'
    "                    )\n"
    "                    return True\n"
    '                if _pgd_action == "rewrite":\n'
    '                    _pgd_text = _pgd.get("text")\n'
    "                    if isinstance(_pgd_text, str):\n"
    "                        event.text = _pgd_text\n"
    "                    break\n"
)


class PreimageError(RuntimeError):
    """Raised when the target source does not match the expected preimage."""


def is_patched(src: str) -> bool:
    """Return True when the busy-path patch is already present."""
    return MARKER in src and _MOD2_INSERT in src


def patch_source(src: str, *, filename: str = "<run.py>") -> str:
    """Return the patched source. Idempotent. Raises PreimageError on mismatch.

    The result is syntax-checked with ``compile`` before being returned.
    """
    if is_patched(src):
        return src
    mod1_count = src.count(_MOD1_PRE)
    if mod1_count != 1:
        raise PreimageError(
            f"MOD1 preimage found {mod1_count} times (expected exactly 1); refusing to patch"
        )
    mod2_count = src.count(_MOD2_ANCHOR)
    if mod2_count != 1:
        raise PreimageError(
            f"MOD2 anchor found {mod2_count} times (expected exactly 1); refusing to patch"
        )
    patched = src.replace(_MOD1_PRE, _MOD1_POST, 1)
    patched = patched.replace(_MOD2_ANCHOR, _MOD2_ANCHOR + _MOD2_INSERT, 1)
    # Fail-closed: never write source that does not compile.
    _ = compile(patched, filename, "exec")
    if not is_patched(patched):  # pragma: no cover - defensive
        raise PreimageError("post-patch verification failed")
    return patched


def verify_source(src: str) -> bool:
    """Return True when both modifications are present in the source."""
    return (
        is_patched(src)
        and _MOD1_POST in src
        and 'session_store=getattr(self, "session_store", None)' in src
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
    tmp = path.with_name(path.name + ".autophagy-tmp")
    _ = tmp.write_text(patched, encoding="utf-8")
    _ = tmp.replace(path)
    print(f"PATCHED {path} (backup: {backup})")
    return 0


def _verify(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    if verify_source(src):
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
    match argv:
        case ("apply", target):
            return _apply(Path(target))
        case ("verify", target):
            return _verify(Path(target))
        case ("revert", target):
            return _revert(Path(target))
        case _:
            print("usage: patch_busy_dispatch.py apply|verify|revert <run.py>", file=sys.stderr)
            return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(tuple(sys.argv[1:])))
    except PreimageError as exc:
        print(f"PREIMAGE-ERROR {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
