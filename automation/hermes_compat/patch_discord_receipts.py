#!/usr/bin/env python3
"""Idempotent, exact-preimage patch for Hermes Discord DM receipts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

MARKER: Final = "_hermes_receipts_done"
BACKUP_SUFFIX: Final = ".autophagy-orig"

_MOD1_PRE: Final = (
    "        if existing is None:\n"
    "            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]\n"
    "            self._pending_text_batches[key] = event\n"
    "        else:\n"
    "            if event.text:\n"
    '                existing.text = f"{existing.text}\\n{event.text}" if existing.text else event.text\n'
    "            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]\n"
    "            if event.media_urls:\n"
    "                existing.media_urls.extend(event.media_urls)\n"
    "                existing.media_types.extend(event.media_types)\n"
)
_MOD1_POST: Final = (
    "        # autophagy hermes_compat (_hermes_receipts_done): retain every physical message in a batch.\n"
    "        try:\n"
    "            import sys as _receipt_sys\n"
    "            _receipt_compat_dir = os.path.expanduser(\"~/.hermes/hermes-compat\")\n"
    "            if _receipt_compat_dir not in _receipt_sys.path:\n"
    "                _receipt_sys.path.insert(0, _receipt_compat_dir)\n"
    "            import hermes_compat_boot\n"
    "            from automation.hermes_compat.receipt_tracker import (\n"
    "                RECEIPT_LAST_TS_KEY as _receipt_last_ts_key,\n"
    "                RECEIPT_MEMBERS_KEY as _receipt_members_key,\n"
    "                RECEIPT_MESSAGE_IDS_KEY as _receipt_message_ids_key,\n"
    "            )\n"
    "        except Exception:\n"
    "            _receipt_members_key = None\n"
    "            _receipt_message_ids_key = None\n"
    "            _receipt_last_ts_key = None\n"
    "        if existing is None:\n"
    "            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]\n"
    "            if _receipt_members_key is not None and _receipt_message_ids_key is not None:\n"
    "                try:\n"
    "                    if not hasattr(event, \"metadata\") or event.metadata is None:\n"
    "                        event.metadata = {}\n"
    "                    event.metadata[_receipt_members_key] = [event.raw_message]\n"
    "                    event.metadata[_receipt_message_ids_key] = [str(getattr(event, \"message_id\", \"\"))]\n"
    "                    event.metadata[_receipt_last_ts_key] = event.timestamp.timestamp()\n"
    "                except Exception:\n"
    "                    pass\n"
    "            self._pending_text_batches[key] = event\n"
    "        else:\n"
    "            if event.text:\n"
    '                existing.text = f"{existing.text}\\n{event.text}" if existing.text else event.text\n'
    "            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]\n"
    "            if event.media_urls:\n"
    "                existing.media_urls.extend(event.media_urls)\n"
    "                existing.media_types.extend(event.media_types)\n"
    "            if _receipt_members_key is not None and _receipt_message_ids_key is not None:\n"
    "                try:\n"
    "                    if not hasattr(existing, \"metadata\") or existing.metadata is None:\n"
    "                        existing.metadata = {}\n"
    "                    existing.metadata.setdefault(_receipt_members_key, [existing.raw_message]).append(event.raw_message)\n"
    "                    existing.metadata.setdefault(\n"
    "                        _receipt_message_ids_key, [str(getattr(existing, \"message_id\", \"\"))]\n"
    "                    ).append(str(getattr(event, \"message_id\", \"\")))\n"
    "                    existing.metadata[_receipt_last_ts_key] = event.timestamp.timestamp()\n"
    "                except Exception:\n"
    "                    pass\n"
)

_MOD2_PRE: Final = (
    '                    _role_authorized = bool(getattr(self, "_allowed_role_ids", set()))\n'
    "                \n"
    "                # Multi-agent filtering: if the message mentions specific bots\n"
)
_MOD2_POST: Final = (
    '                    _role_authorized = bool(getattr(self, "_allowed_role_ids", set()))\n'
    "                    # autophagy hermes_compat (_hermes_receipts_done: owner DM receipt)\n"
    "                    if _is_dm:\n"
    "                        try:\n"
    "                            if adapter_self._reactions_enabled():\n"
    "                                await adapter_self._add_reaction(message, \"👀\")\n"
    "                        except Exception:\n"
    "                            pass\n"
    "                        try:\n"
    "                            import sys as _receipt_sys\n"
    "                            _receipt_compat_dir = os.path.expanduser(\"~/.hermes/hermes-compat\")\n"
    "                            if _receipt_compat_dir not in _receipt_sys.path:\n"
    "                                _receipt_sys.path.insert(0, _receipt_compat_dir)\n"
    "                            import hermes_compat_boot\n"
    "                            from automation.hermes_compat.receipt_ledger import ReceiptLedger as _ReceiptLedger\n"
    "                            from automation.hermes_compat.receipt_ledger import default_ledger_path as _default_ledger_path\n"
    "                            _receipt_ledger = _ReceiptLedger(_default_ledger_path())\n"
    "                            _receipt_ledger.record_received(\n"
    "                                str(getattr(message.channel, \"id\", \"\")), str(getattr(message, \"id\", \"\"))\n"
    "                            )\n"
    "                        except Exception:\n"
    "                            pass\n"
    "                \n"
    "                # Multi-agent filtering: if the message mentions specific bots\n"
)

_MOD3_PRE: Final = (
    "    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:\n"
    '        """Swap the in-progress reaction for a final success/failure reaction."""\n'
    "        if not self._reactions_enabled():\n"
    "            return\n"
    "        message = event.raw_message\n"
    "        if hasattr(message, \"add_reaction\"):\n"
    "            await self._remove_reaction(message, \"👀\")\n"
    "            if outcome == ProcessingOutcome.SUCCESS:\n"
    "                await self._add_reaction(message, \"✅\")\n"
    "            elif outcome == ProcessingOutcome.FAILURE:\n"
    "                await self._add_reaction(message, \"❌\")\n"
)
_MOD3_POST: Final = (
    "    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:\n"
    '        """Finalize every physical owner DM of the completing turn (autophagy: per-DM \u2705/\u274c + ledger)."""\n'
    "        # autophagy hermes_compat (_hermes_receipts_done): shared idempotent resolve boundary.\n"
    "        try:\n"
    "            import sys as _receipt_sys\n"
    "            _receipt_compat_dir = os.path.expanduser(\"~/.hermes/hermes-compat\")\n"
    "            if _receipt_compat_dir not in _receipt_sys.path:\n"
    "                _receipt_sys.path.insert(0, _receipt_compat_dir)\n"
    "            import hermes_compat_boot\n"
    "            from automation.hermes_compat.receipt_apply import resolve_receipts as _resolve_receipts\n"
    "        except Exception:\n"
    "            _resolve_receipts = None\n"
    "        if _resolve_receipts is not None:\n"
    "            try:\n"
    "                await _resolve_receipts(self, event, ok=outcome == ProcessingOutcome.SUCCESS)\n"
    "            except Exception:\n"
    "                pass\n"
)


class PreimageError(RuntimeError):
    """Raised when target source differs from an expected exact preimage."""


def is_patched(src: str) -> bool:
    """Return True only when all receipt modifications are present."""
    return MARKER in src and all(post in src for post in (_MOD1_POST, _MOD2_POST, _MOD3_POST))


def patch_source(src: str, *, filename: str = "<adapter.py>") -> str:
    """Return a compile-gated patch or raise PreimageError on source drift."""
    if is_patched(src):
        return src
    for name, preimage in (("MOD1", _MOD1_PRE), ("MOD2", _MOD2_PRE), ("MOD3", _MOD3_PRE)):
        count = src.count(preimage)
        if count != 1:
            raise PreimageError(
                f"{name} preimage found {count} times (expected exactly 1); refusing to patch"
            )
    patched = src.replace(_MOD1_PRE, _MOD1_POST, 1)
    patched = patched.replace(_MOD2_PRE, _MOD2_POST, 1)
    patched = patched.replace(_MOD3_PRE, _MOD3_POST, 1)
    _ = compile(patched, filename, "exec")
    if not is_patched(patched):
        raise PreimageError("post-patch verification failed")
    return patched


def verify_source(src: str) -> bool:
    """Return True when all three receipt behaviours are installed."""
    return (
        is_patched(src)
        and "event.metadata[_receipt_members_key] = [event.raw_message]" in src
        and "event.metadata[_receipt_last_ts_key] = event.timestamp.timestamp()" in src
        and "_receipt_ledger.record_received(" in src
        and "from automation.hermes_compat.receipt_apply import resolve_receipts" in src
        and "await _resolve_receipts(self, event, ok=" in src
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
    """Execute apply, verify, or revert against one vendored adapter file."""
    match argv:
        case ("apply", target):
            return _apply(Path(target))
        case ("verify", target):
            return _verify(Path(target))
        case ("revert", target):
            return _revert(Path(target))
        case _:
            print("usage: patch_discord_receipts.py apply|verify|revert <adapter.py>", file=sys.stderr)
            return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(tuple(sys.argv[1:])))
    except PreimageError as exc:
        print(f"PREIMAGE-ERROR {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
