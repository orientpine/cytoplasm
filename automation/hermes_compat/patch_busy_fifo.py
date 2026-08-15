#!/usr/bin/env python3
"""Idempotent, exact-preimage patch for Hermes owner-DM busy FIFO routing."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

MARKER: Final = "_hermes_busyfifo_done"
BACKUP_SUFFIX: Final = ".autophagy-orig"

_MOD1_PRE: Final = (
    "        effective_mode = self._busy_input_mode\n"
    '        busy_text_mode = getattr(self, "_busy_text_mode", "interrupt")\n'
    "        if (\n"
    "            event.message_type == MessageType.TEXT\n"
    '            and busy_text_mode == "queue"\n'
    '            and effective_mode != "steer"\n'
    "        ):\n"
    "            return False\n"
)
_MOD1_POST: Final = (
    "        effective_mode = self._busy_input_mode\n"
    '        busy_text_mode = getattr(self, "_busy_text_mode", "interrupt")\n'
    "        if (\n"
    "            event.message_type == MessageType.TEXT\n"
    '            and busy_text_mode == "queue"\n'
    '            and effective_mode != "steer"\n'
    "        ):\n"
    "            try:\n"
    "                # autophagy hermes_compat (_hermes_busyfifo_done: owner DM FIFO)\n"
    "                _source = event.source\n"
    '                _is_dm = getattr(_source, "chat_type", "") == "dm" and getattr(\n'
    '                    getattr(_source, "platform", None), "value", ""\n'
    '                ) == "discord"\n'
    "                if not _is_dm:\n"
    "                    return False\n"
    "                import sys as _busy_fifo_sys\n"
    '                _busy_fifo_compat_dir = os.path.expanduser("~/.hermes/hermes-compat")\n'
    "                if _busy_fifo_compat_dir not in _busy_fifo_sys.path:\n"
    "                    _busy_fifo_sys.path.insert(0, _busy_fifo_compat_dir)\n"
    "                import hermes_compat_boot\n"
    "                from automation.hermes_compat.owner_dm_signal import relatedness_for\n"
    "                from automation.hermes_compat.owner_dm_dispatch import route, RouteOutcome\n"
    "                from automation.hermes_compat.receipt_tracker import (\n"
    "                    RECEIPT_LAST_TS_KEY,\n"
    "                    RECEIPT_MESSAGE_IDS_KEY,\n"
    "                )\n"
    "                from automation.hermes_compat.receipt_apply import resolve_receipts as _busy_fifo_resolve\n"
    "                _overflow = self._queued_events\n"
    "                _pending = adapter._pending_messages\n"
    "                _tail = (_overflow.get(session_key) or [None])[-1] "
    "if _overflow.get(session_key) else _pending.get(session_key)\n"
    "                _tail_id = None\n"
    "                if _tail is not None:\n"
    '                    _ids = getattr(_tail, "metadata", {}).get(\n'
    "                        RECEIPT_MESSAGE_IDS_KEY\n"
    "                    ) or []\n"
    "                    _tail_id = _ids[-1] if _ids else None\n"
    '                _meta = getattr(event, "metadata", None) or {}\n'
    "                _last_ts = _meta.get(RECEIPT_LAST_TS_KEY) or event.timestamp.timestamp()\n"
    "                _rel = relatedness_for(\n"
    "                    session_key,\n"
    '                    owner_id=str(getattr(_source, "user_id", "")),\n'
    "                    timestamp=event.timestamp.timestamp(),\n"
    '                    reply_to_message_id=getattr(event, "reply_to_message_id", None),\n'
    '                    has_media=bool(getattr(event, "media_urls", None)),\n'
    '                    is_internal=bool(getattr(event, "internal", False)),\n'
    "                    tail_message_id=_tail_id,\n"
    "                    last_physical_timestamp=_last_ts,\n"
    "                )\n"
    "                _outcome = route(\n"
    "                    _pending, _overflow, session_key, event, _rel,\n"
    "                    cap=self._BUSY_QUEUE_MAX_PENDING,\n"
    "                )\n"
    "                if _outcome is RouteOutcome.REJECTED_OVER_CAP:\n"
    "                    try:\n"
    "                        await _busy_fifo_resolve(adapter, event, ok=False)\n"
    "                    except Exception:\n"
    "                        pass\n"
    "                    try:\n"
    "                        await adapter._send_with_retry(\n"
    "                            chat_id=_source.chat_id,\n"
    '                            content="❌ 대기열이 가득 차 이 메시지를 받지 못했어요 — 잠시 후 다시 보내주세요.",\n'
    "                        )\n"
    "                    except Exception:\n"
    "                        pass\n"
    "                return True\n"
    "            except Exception:\n"
    "                logger.warning(\n"
    '                    "owner-DM busy-fifo routing failed for %s; deferring",\n'
    "                    session_key,\n"
    "                    exc_info=True,\n"
    "                )\n"
    "            return False\n"
)

_MOD2_PRE: Final = (
    "                    if adapter and pending_event:\n"
    "                        merge_pending_message_event("
    "adapter._pending_messages, session_key, pending_event)\n"
    "                    elif adapter and hasattr(adapter, 'queue_message'):\n"
)
_MOD2_POST: Final = (
    "                    if adapter and pending_event:\n"
    "                        try:\n"
    "                            # autophagy hermes_compat (_hermes_busyfifo_done: prepend)\n"
    "                            import sys as _busy_fifo_sys\n"
    '                            _busy_fifo_compat_dir = os.path.expanduser("~/.hermes/hermes-compat")\n'
    "                            if _busy_fifo_compat_dir not in _busy_fifo_sys.path:\n"
    "                                _busy_fifo_sys.path.insert(0, _busy_fifo_compat_dir)\n"
    "                            import hermes_compat_boot\n"
    "                            from automation.hermes_compat.owner_dm_dispatch import prepend\n"
    "                            prepend(adapter._pending_messages, self._queued_events, "
    "session_key, pending_event)\n"
    "                        except Exception:\n"
    "                            merge_pending_message_event("
    "adapter._pending_messages, session_key, pending_event)\n"
    "                    elif adapter and hasattr(adapter, 'queue_message'):\n"
)

_MOD3_PRE: Final = (
    "            result = result_holder[0]\n"
    "            adapter = self._adapter_for_source(source)\n"
)
_MOD3_POST: Final = (
    "            result = result_holder[0]\n"
    "            adapter = self._adapter_for_source(source)\n"
    "            try:\n"
    "                # autophagy hermes_compat (_hermes_busyfifo_done: per-turn follow-up receipt)\n"
    '                _rr_map = getattr(self, "_autophagy_receipt_by_depth", None)\n'
    "                _rr_event = _rr_map.pop((session_key, _interrupt_depth), None) if _rr_map else None\n"
    "                if _rr_event is not None and adapter is not None:\n"
    "                    import sys as _busy_fifo_sys\n"
    '                    _busy_fifo_compat_dir = os.path.expanduser("~/.hermes/hermes-compat")\n'
    "                    if _busy_fifo_compat_dir not in _busy_fifo_sys.path:\n"
    "                        _busy_fifo_sys.path.insert(0, _busy_fifo_compat_dir)\n"
    "                    import hermes_compat_boot\n"
    "                    from automation.hermes_compat.receipt_apply import resolve_receipts as _busy_fifo_resolve\n"
    '                    _busy_fifo_ok = bool(result) and not result.get("failed", False)\n'
    "                    await _busy_fifo_resolve(adapter, _rr_event, ok=_busy_fifo_ok)\n"
    "            except Exception:\n"
    "                pass\n"
)

_MOD4_PRE: Final = (
    '        logger.info("Starting Hermes Gateway...")\n'
    "        try:\n"
)
_MOD4_POST: Final = (
    '        logger.info("Starting Hermes Gateway...")\n'
    "        try:\n"
    "            # autophagy hermes_compat (_hermes_busyfifo_done: startup receipt reconcile)\n"
    "            import sys as _hc_reconcile_sys\n"
    '            _hc_reconcile_dir = os.path.expanduser("~/.hermes/hermes-compat")\n'
    "            if _hc_reconcile_dir not in _hc_reconcile_sys.path:\n"
    "                _hc_reconcile_sys.path.insert(0, _hc_reconcile_dir)\n"
    "            import hermes_compat_boot\n"
    "            from automation.hermes_compat.receipt_ledger import ReceiptLedger as _HcLedger\n"
    "            from automation.hermes_compat.receipt_ledger import default_ledger_path as _hc_ledger_path\n"
    "            _HcLedger(_hc_ledger_path()).reconcile_unresolved()\n"
    "        except Exception:\n"
    "            pass\n"
    "        try:\n"
)

_MOD5_PRE: Final = (
    "                await self._refresh_agent_cache_message_count(session_key, session_id)\n"
    "\n"
    "                followup_result = await self._run_agent(\n"
    "                    message=next_message,\n"
    "                    context_prompt=context_prompt,\n"
    "                    history=updated_history,\n"
    "                    source=next_source,\n"
    "                    session_id=session_id,\n"
    "                    session_key=next_session_key,\n"
    "                    run_generation=run_generation,\n"
    "                    _interrupt_depth=_interrupt_depth + 1,\n"
    "                    event_message_id=next_message_id,\n"
    "                    channel_prompt=next_channel_prompt,\n"
    "                )\n"
    "                return _preserve_queued_followup_history_offset(result, followup_result)\n"
)
_MOD5_POST: Final = (
    "                await self._refresh_agent_cache_message_count(session_key, session_id)\n"
    "\n"
    "                try:\n"
    "                    # autophagy hermes_compat (_hermes_busyfifo_done: stash follow-up event for its own frame)\n"
    "                    if pending_event is not None:\n"
    '                        _rr_stash = getattr(self, "_autophagy_receipt_by_depth", None)\n'
    "                        if _rr_stash is None:\n"
    "                            _rr_stash = {}\n"
    "                            self._autophagy_receipt_by_depth = _rr_stash\n"
    "                        _rr_stash[(next_session_key, _interrupt_depth + 1)] = pending_event\n"
    "                except Exception:\n"
    "                    pass\n"
    "                try:\n"
    "                    followup_result = await self._run_agent(\n"
    "                        message=next_message,\n"
    "                        context_prompt=context_prompt,\n"
    "                        history=updated_history,\n"
    "                        source=next_source,\n"
    "                        session_id=session_id,\n"
    "                        session_key=next_session_key,\n"
    "                        run_generation=run_generation,\n"
    "                        _interrupt_depth=_interrupt_depth + 1,\n"
    "                        event_message_id=next_message_id,\n"
    "                        channel_prompt=next_channel_prompt,\n"
    "                    )\n"
    "                finally:\n"
    "                    try:\n"
    "                        # autophagy hermes_compat: if the follow-up frame exited before finalizing\n"
    "                        # its own receipt (exception/cancel), finalize it failed so no DM stays 👀.\n"
    '                        _rr_cleanup = getattr(self, "_autophagy_receipt_by_depth", None)\n'
    "                        _rr_orphan = _rr_cleanup.pop((next_session_key, _interrupt_depth + 1), None) if _rr_cleanup else None\n"
    "                        if _rr_orphan is not None:\n"
    "                            _rr_adapter = self._adapter_for_source(next_source)\n"
    "                            if _rr_adapter is not None:\n"
    "                                import sys as _busy_fifo_sys\n"
    '                                _busy_fifo_compat_dir = os.path.expanduser("~/.hermes/hermes-compat")\n'
    "                                if _busy_fifo_compat_dir not in _busy_fifo_sys.path:\n"
    "                                    _busy_fifo_sys.path.insert(0, _busy_fifo_compat_dir)\n"
    "                                import hermes_compat_boot\n"
    "                                from automation.hermes_compat.receipt_apply import resolve_receipts as _busy_fifo_resolve\n"
    "                                await _busy_fifo_resolve(_rr_adapter, _rr_orphan, ok=False)\n"
    "                    except Exception:\n"
    "                        pass\n"
    "                return _preserve_queued_followup_history_offset(result, followup_result)\n"
)


class PreimageError(RuntimeError):
    """Raised when target source differs from an expected exact preimage."""


def is_patched(src: str) -> bool:
    """Return True only when both busy-FIFO modifications are present."""
    return MARKER in src and all(
        post in src for post in (_MOD1_POST, _MOD2_POST, _MOD3_POST, _MOD4_POST, _MOD5_POST)
    )


def patch_source(src: str, *, filename: str = "<run.py>") -> str:
    """Return a compile-gated patch or raise PreimageError on source drift."""
    if is_patched(src):
        return src
    for name, preimage in (
        ("MOD1", _MOD1_PRE),
        ("MOD2", _MOD2_PRE),
        ("MOD3", _MOD3_PRE),
        ("MOD4", _MOD4_PRE),
        ("MOD5", _MOD5_PRE),
    ):
        count = src.count(preimage)
        if count != 1:
            raise PreimageError(
                f"{name} preimage found {count} times (expected exactly 1); refusing to patch"
            )
    patched = src.replace(_MOD1_PRE, _MOD1_POST, 1)
    patched = patched.replace(_MOD2_PRE, _MOD2_POST, 1)
    patched = patched.replace(_MOD3_PRE, _MOD3_POST, 1)
    patched = patched.replace(_MOD4_PRE, _MOD4_POST, 1)
    patched = patched.replace(_MOD5_PRE, _MOD5_POST, 1)
    _ = compile(patched, filename, "exec")
    if not is_patched(patched):
        raise PreimageError("post-patch verification failed")
    return patched


def verify_source(src: str) -> bool:
    """Return True when relatedness routing and depth-cap prepend are installed."""
    return (
        is_patched(src)
        and "_outcome = route(" in src
        and "prepend(adapter._pending_messages, self._queued_events" in src
        and "await _busy_fifo_resolve(adapter, _rr_event, ok=" in src
        and "_rr_stash[(next_session_key, _interrupt_depth + 1)] = pending_event" in src
        and "await _busy_fifo_resolve(_rr_adapter, _rr_orphan, ok=False)" in src
        and "_HcLedger(_hc_ledger_path()).reconcile_unresolved()" in src
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
    """Execute apply, verify, or revert against one vendored gateway file."""
    if len(argv) == 2:
        command, target = argv
        handler = {"apply": _apply, "verify": _verify, "revert": _revert}.get(command)
        if handler is not None:
            return handler(Path(target))
    print("usage: patch_busy_fifo.py apply|verify|revert <run.py>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(tuple(sys.argv[1:])))
    except PreimageError as exc:
        print(f"PREIMAGE-ERROR {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
