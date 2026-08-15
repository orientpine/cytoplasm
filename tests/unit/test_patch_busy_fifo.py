"""Tests for the Hermes owner-DM busy FIFO compatibility patch."""

from __future__ import annotations

import difflib
import importlib.util
import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import pytest

if TYPE_CHECKING:

    class _Patcher:
        BACKUP_SUFFIX: ClassVar[str] = ""
        PreimageError: ClassVar[type[RuntimeError]] = RuntimeError

        def is_patched(self, src: str) -> bool:
            _ = src
            return False

        def patch_source(self, src: str, *, filename: str) -> str:
            _ = filename
            return src

        def verify_source(self, src: str) -> bool:
            _ = src
            return False

        def main(self, argv: tuple[str, ...]) -> int:
            _ = argv
            return 0

    patcher = _Patcher()
else:
    _MODULE_PATH = (
        Path(__file__).resolve().parents[2]
        / "automation"
        / "hermes_compat"
        / "patch_busy_fifo.py"
    )
    _spec = importlib.util.spec_from_file_location("patch_busy_fifo", _MODULE_PATH)
    assert _spec is not None and _spec.loader is not None
    patcher = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(patcher)


# Minimal valid source embedding the reviewed <primary-node> run.py anchors byte-for-byte.
FIXTURE = '''\
class GatewayRunner:
    async def _handle_active_session_busy_message(self, event, session_key):
        adapter = self._adapter_for_source(event.source)
        effective_mode = self._busy_input_mode
        busy_text_mode = getattr(self, "_busy_text_mode", "interrupt")
        if (
            event.message_type == MessageType.TEXT
            and busy_text_mode == "queue"
            and effective_mode != "steer"
        ):
            return False
        return True

    async def _run_agent(self, source, session_key, session_id, _interrupt_depth, pending_event, pending, next_session_key, result_holder):
        try:
            result = result_holder[0]
            adapter = self._adapter_for_source(source)
            if pending_event or pending:
                if _interrupt_depth >= self._MAX_INTERRUPT_DEPTH:
                    adapter = self._adapter_for_source(source)
                    if adapter and pending_event:
                        merge_pending_message_event(adapter._pending_messages, session_key, pending_event)
                    elif adapter and hasattr(adapter, 'queue_message'):
                        adapter.queue_message(session_key, pending)
                await self._refresh_agent_cache_message_count(session_key, session_id)

                followup_result = await self._run_agent(
                    message=next_message,
                    context_prompt=context_prompt,
                    history=updated_history,
                    source=next_source,
                    session_id=session_id,
                    session_key=next_session_key,
                    run_generation=run_generation,
                    _interrupt_depth=_interrupt_depth + 1,
                    event_message_id=next_message_id,
                    channel_prompt=next_channel_prompt,
                )
                return _preserve_queued_followup_history_offset(result, followup_result)
        finally:
            pass

    async def start(self):
        logger.info("Starting Hermes Gateway...")
        try:
            self._gateway_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._gateway_loop = None
'''

_MOD1_PREIMAGE = '''\
        effective_mode = self._busy_input_mode
        busy_text_mode = getattr(self, "_busy_text_mode", "interrupt")
        if (
            event.message_type == MessageType.TEXT
            and busy_text_mode == "queue"
            and effective_mode != "steer"
        ):
            return False
'''
_MOD2_PREIMAGE = '''\
                    if adapter and pending_event:
                        merge_pending_message_event(adapter._pending_messages, session_key, pending_event)
                    elif adapter and hasattr(adapter, 'queue_message'):
'''
_MOD3_PREIMAGE = '''\
            result = result_holder[0]
            adapter = self._adapter_for_source(source)
'''
_MOD4_PREIMAGE = '''\
        logger.info("Starting Hermes Gateway...")
        try:
'''
_MOD5_PREIMAGE = '''\
                await self._refresh_agent_cache_message_count(session_key, session_id)

                followup_result = await self._run_agent(
                    message=next_message,
                    context_prompt=context_prompt,
                    history=updated_history,
                    source=next_source,
                    session_id=session_id,
                    session_key=next_session_key,
                    run_generation=run_generation,
                    _interrupt_depth=_interrupt_depth + 1,
                    event_message_id=next_message_id,
                    channel_prompt=next_channel_prompt,
                )
                return _preserve_queued_followup_history_offset(result, followup_result)
'''
_PREIMAGES = (_MOD1_PREIMAGE, _MOD2_PREIMAGE, _MOD3_PREIMAGE, _MOD4_PREIMAGE, _MOD5_PREIMAGE)


def test_patch_routes_owner_dm_fifo_and_compiles() -> None:
    # Given / When
    patched = patcher.patch_source(FIXTURE, filename="run.py")

    # Then
    assert patcher.is_patched(patched)
    assert patcher.verify_source(patched)
    assert _MOD1_PREIMAGE not in patched
    assert _MOD2_PREIMAGE not in patched
    assert "from automation.hermes_compat.owner_dm_signal import relatedness_for" in patched
    assert "from automation.hermes_compat.owner_dm_dispatch import route, RouteOutcome" in patched
    assert "_outcome = route(" in patched
    assert "_outcome is RouteOutcome.REJECTED_OVER_CAP" in patched
    assert "from automation.hermes_compat.owner_dm_dispatch import prepend" in patched
    assert "prepend(adapter._pending_messages, self._queued_events" in patched
    assert (
        "from automation.hermes_compat.receipt_apply import resolve_receipts as _busy_fifo_resolve"
    ) in patched
    assert "await _busy_fifo_resolve(adapter, _rr_event, ok=" in patched
    assert "_rr_event = _rr_map.pop((session_key, _interrupt_depth), None)" in patched
    assert "_rr_stash[(next_session_key, _interrupt_depth + 1)] = pending_event" in patched
    assert "await _busy_fifo_resolve(_rr_adapter, _rr_orphan, ok=False)" in patched
    assert "await _busy_fifo_resolve(adapter, event, ok=False)" in patched
    assert "last_physical_timestamp=_last_ts," in patched
    assert "_last_ts = _meta.get(RECEIPT_LAST_TS_KEY) or event.timestamp.timestamp()" in patched
    assert "_HcLedger(_hc_ledger_path()).reconcile_unresolved()" in patched
    _ = compile(patched, "run.py", "exec")


def test_mod1_replaces_dm_seam_return_false() -> None:
    # Given / When
    patched = patcher.patch_source(FIXTURE, filename="run.py")

    # Then
    seam = _MOD1_PREIMAGE.removesuffix("            return False\n")
    assert seam + "            try:\n" in patched
    assert seam + "            return False\n" not in patched
    assert '                if not _is_dm:\n                    return False\n' in patched
    assert "                return True\n" in patched


def test_injected_code_never_interrupts_or_steers() -> None:
    # Given
    patched = patcher.patch_source(FIXTURE, filename="run.py")
    original_lines = FIXTURE.splitlines(keepends=True)
    patched_lines = patched.splitlines(keepends=True)

    # When
    injected = "".join(
        line
        for tag, _, _, patched_start, patched_end in difflib.SequenceMatcher(
            a=original_lines,
            b=patched_lines,
        ).get_opcodes()
        if tag in {"insert", "replace"}
        for line in patched_lines[patched_start:patched_end]
    )

    # Then
    assert "interrupt(" not in injected
    assert ".steer(" not in injected
    assert re.search(r"(?<![.\w])interrupt\s*\(", injected) is None


def test_patch_is_idempotent() -> None:
    # Given / When
    once = patcher.patch_source(FIXTURE, filename="run.py")
    twice = patcher.patch_source(once, filename="run.py")

    # Then
    assert once == twice


@pytest.mark.parametrize("preimage", _PREIMAGES)
def test_missing_preimage_refuses(preimage: str) -> None:
    # Given
    broken = FIXTURE.replace(preimage, "", 1)

    # When / Then
    with pytest.raises(patcher.PreimageError):
        _ = patcher.patch_source(broken, filename="run.py")


@pytest.mark.parametrize("preimage", _PREIMAGES)
def test_duplicate_preimage_refuses(preimage: str) -> None:
    # Given
    duplicate = FIXTURE.replace(preimage, preimage + preimage, 1)

    # When / Then
    with pytest.raises(patcher.PreimageError):
        _ = patcher.patch_source(duplicate, filename="run.py")


def test_apply_verify_revert_roundtrip_restores_exact_bytes(tmp_path: Path) -> None:
    # Given
    target = tmp_path / "run.py"
    _ = target.write_text(FIXTURE, encoding="utf-8")

    # When
    assert patcher.main(("apply", str(target))) == 0
    backup = target.with_name("run.py" + patcher.BACKUP_SUFFIX)

    # Then
    assert backup.read_text(encoding="utf-8") == FIXTURE
    assert patcher.main(("verify", str(target))) == 0
    assert patcher.main(("revert", str(target))) == 0
    assert target.read_text(encoding="utf-8") == FIXTURE


def test_verify_fails_for_unpatched_file(tmp_path: Path) -> None:
    # Given
    target = tmp_path / "run.py"
    _ = target.write_text(FIXTURE, encoding="utf-8")

    # When / Then
    assert patcher.main(("verify", str(target))) == 3
