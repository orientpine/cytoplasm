"""Tests for the Hermes Discord physical-message receipt compatibility patch."""

from __future__ import annotations

import importlib.util
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
        / "patch_discord_receipts.py"
    )
    _spec = importlib.util.spec_from_file_location("patch_discord_receipts", _MODULE_PATH)
    assert _spec is not None and _spec.loader is not None
    patcher = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(patcher)


# A minimal, syntactically valid stand-in embedding the live Discord adapter
# anchors byte-for-byte. The node dry-run refuses if Hermes upstream drifts.
FIXTURE = '''\
from __future__ import annotations


class Adapter:
    async def on_message(self, message):
        if True:
            if True:
                if True:
                    _role_authorized = bool(getattr(self, "_allowed_role_ids", set()))
                
                # Multi-agent filtering: if the message mentions specific bots
                pass

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        """Swap the in-progress reaction for a final success/failure reaction."""
        if not self._reactions_enabled():
            return
        message = event.raw_message
        if hasattr(message, "add_reaction"):
            await self._remove_reaction(message, "👀")
            if outcome == ProcessingOutcome.SUCCESS:
                await self._add_reaction(message, "✅")
            elif outcome == ProcessingOutcome.FAILURE:
                await self._add_reaction(message, "❌")

    def _enqueue_text_event(self, event: MessageEvent) -> None:
        key = self._text_batch_key(event)
        existing = self._pending_text_batches.get(key)
        chunk_len = len(event.text or "")
        if existing is None:
            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            self._pending_text_batches[key] = event
        else:
            if event.text:
                existing.text = f"{existing.text}\\n{event.text}" if existing.text else event.text
            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            if event.media_urls:
                existing.media_urls.extend(event.media_urls)
                existing.media_types.extend(event.media_types)
'''

_INGRESS_PREIMAGE = '''\
        if existing is None:
            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            self._pending_text_batches[key] = event
        else:
            if event.text:
                existing.text = f"{existing.text}\\n{event.text}" if existing.text else event.text
            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            if event.media_urls:
                existing.media_urls.extend(event.media_urls)
                existing.media_types.extend(event.media_types)
'''
_RECEIVE_PREIMAGE = '''\
                    _role_authorized = bool(getattr(self, "_allowed_role_ids", set()))
                
                # Multi-agent filtering: if the message mentions specific bots
'''
_LIFECYCLE_PREIMAGE = '''\
    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        """Swap the in-progress reaction for a final success/failure reaction."""
        if not self._reactions_enabled():
            return
        message = event.raw_message
        if hasattr(message, "add_reaction"):
            await self._remove_reaction(message, "👀")
            if outcome == ProcessingOutcome.SUCCESS:
                await self._add_reaction(message, "✅")
            elif outcome == ProcessingOutcome.FAILURE:
                await self._add_reaction(message, "❌")
'''
_PREIMAGES = (_INGRESS_PREIMAGE, _RECEIVE_PREIMAGE, _LIFECYCLE_PREIMAGE)


def test_patch_applies_all_receipt_modifications_and_compiles() -> None:
    # Given / When
    patched = patcher.patch_source(FIXTURE, filename="adapter.py")

    # Then
    assert patcher.is_patched(patched)
    assert patcher.verify_source(patched)
    assert 'event.metadata[_receipt_members_key] = [event.raw_message]' in patched
    assert "event.metadata[_receipt_last_ts_key] = event.timestamp.timestamp()" in patched
    assert "existing.metadata[_receipt_last_ts_key] = event.timestamp.timestamp()" in patched
    assert (
        "existing.metadata.setdefault(_receipt_members_key, [existing.raw_message]).append("
        "event.raw_message)"
    ) in patched
    assert "await adapter_self._add_reaction(message, \"👀\")" in patched
    assert "_receipt_ledger.record_received(" in patched
    assert "from automation.hermes_compat.receipt_apply import resolve_receipts" in patched
    assert "await _resolve_receipts(self, event, ok=" in patched
    _ = compile(patched, "adapter.py", "exec")


def test_receipt_injections_run_in_required_lifecycle_order() -> None:
    # Given / When
    patched = patcher.patch_source(FIXTURE, filename="adapter.py")

    # Then
    assert patched.index("_hermes_receipts_done: owner DM receipt") > patched.index(
        "_role_authorized ="
    )
    assert patched.index("_hermes_receipts_done: owner DM receipt") < patched.index(
        "# Multi-agent filtering"
    )
    assert patched.index("event.metadata[_receipt_members_key]") < patched.index(
        "self._pending_text_batches[key] = event"
    )
    assert patched.index(
        "from automation.hermes_compat.receipt_apply import resolve_receipts"
    ) < patched.index("await _resolve_receipts(self, event, ok=")


def test_patch_is_idempotent() -> None:
    # Given / When
    once = patcher.patch_source(FIXTURE, filename="adapter.py")
    twice = patcher.patch_source(once, filename="adapter.py")

    # Then
    assert once == twice


@pytest.mark.parametrize("preimage", _PREIMAGES)
def test_missing_preimage_refuses(preimage: str) -> None:
    # Given
    broken = FIXTURE.replace(preimage, "", 1)

    # When / Then
    with pytest.raises(patcher.PreimageError):
        _ = patcher.patch_source(broken, filename="adapter.py")


@pytest.mark.parametrize("preimage", _PREIMAGES)
def test_duplicate_preimage_refuses(preimage: str) -> None:
    # Given
    duplicate = FIXTURE.replace(preimage, preimage + preimage, 1)

    # When / Then
    with pytest.raises(patcher.PreimageError):
        _ = patcher.patch_source(duplicate, filename="adapter.py")


def test_apply_verify_revert_roundtrip_restores_exact_bytes(tmp_path: Path) -> None:
    # Given
    target = tmp_path / "adapter.py"
    _ = target.write_text(FIXTURE, encoding="utf-8")

    # When
    assert patcher.main(("apply", str(target))) == 0
    backup = target.with_name("adapter.py" + patcher.BACKUP_SUFFIX)

    # Then
    assert backup.read_text(encoding="utf-8") == FIXTURE
    assert patcher.main(("verify", str(target))) == 0
    assert patcher.main(("revert", str(target))) == 0
    assert target.read_text(encoding="utf-8") == FIXTURE


def test_verify_fails_for_unpatched_file(tmp_path: Path) -> None:
    # Given
    target = tmp_path / "adapter.py"
    _ = target.write_text(FIXTURE, encoding="utf-8")

    # When / Then
    assert patcher.main(("verify", str(target))) == 3
