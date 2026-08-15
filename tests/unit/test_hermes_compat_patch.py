"""Tests for the Hermes busy-path pre_gateway_dispatch compatibility patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "automation"
    / "hermes_compat"
    / "patch_busy_dispatch.py"
)
_spec = importlib.util.spec_from_file_location("patch_busy_dispatch", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
patcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patcher)


# A minimal, syntactically valid stand-in that embeds the two real anchors
# byte-for-byte. If the real gateway/run.py drifts, the on-node exact-preimage
# apply refuses; this fixture pins the strings the applier depends on.
FIXTURE = '''\
import dataclasses


class GatewayRunner:
    async def _handle_message(self, event, source, is_internal):
        if not is_internal:
            try:
                from hermes_cli.plugins import invoke_hook as _invoke_hook
                _hook_results = _invoke_hook(
                    "pre_gateway_dispatch",
                    event=event,
                    gateway=self,
                    session_store=self.session_store,
                )
            except Exception as _hook_exc:
                logger.warning("pre_gateway_dispatch invocation failed: %s", _hook_exc)
                _hook_results = []

            for _result in _hook_results:
                if not isinstance(_result, dict):
                    continue
                _action = _result.get("action")
                if _action == "skip":
                    return None
                if _action == "rewrite":
                    _new_text = _result.get("text")
                    if isinstance(_new_text, str):
                        event = dataclasses.replace(event, text=_new_text)
                        source = event.source
                    break
                if _action == "allow":
                    break
        return None

    async def _handle_active_session_busy_message(self, event: MessageEvent, session_key: str) -> bool:
        if not self._is_user_authorized(event.source):
            return True
        return False
'''


def test_patch_applies_both_modifications_and_compiles() -> None:
    patched = patcher.patch_source(FIXTURE, filename="run.py")
    assert patcher.is_patched(patched)
    assert patcher.verify_source(patched)
    # Marker-guarded idle block.
    assert 'if not is_internal and not event.metadata.get("_hermes_pgd_done"):' in patched
    assert 'event.metadata["_hermes_pgd_done"] = True' in patched
    # Busy-path invocation.
    assert 'session_store=getattr(self, "session_store", None)' in patched
    # Result still compiles.
    compile(patched, "run.py", "exec")


def test_busy_hook_fires_before_authorization_gate() -> None:
    patched = patcher.patch_source(FIXTURE, filename="run.py")
    insert_at = patched.index("busy-path pre_gateway_dispatch")
    auth_at = patched.index("if not self._is_user_authorized(event.source):")
    assert insert_at < auth_at, "hook must run before the busy-handler auth gate"


def test_patch_is_idempotent() -> None:
    once = patcher.patch_source(FIXTURE, filename="run.py")
    twice = patcher.patch_source(once, filename="run.py")
    assert once == twice


def test_missing_mod1_preimage_refuses() -> None:
    broken = FIXTURE.replace("_hook_results = _invoke_hook(", "_hook_results = something_else(")
    with pytest.raises(patcher.PreimageError):
        patcher.patch_source(broken, filename="run.py")


def test_missing_mod2_anchor_refuses() -> None:
    broken = FIXTURE.replace("_handle_active_session_busy_message", "_handle_renamed_busy")
    with pytest.raises(patcher.PreimageError):
        patcher.patch_source(broken, filename="run.py")


def test_duplicate_anchor_refuses() -> None:
    dup = FIXTURE + "\n" + FIXTURE
    with pytest.raises(patcher.PreimageError):
        patcher.patch_source(dup, filename="run.py")


def test_apply_verify_revert_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "run.py"
    _ = target.write_text(FIXTURE, encoding="utf-8")

    assert patcher.main(("apply", str(target))) == 0
    backup = target.with_name("run.py" + patcher.BACKUP_SUFFIX)
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == FIXTURE
    assert patcher.verify_source(target.read_text(encoding="utf-8"))

    # verify subcommand passes on patched file.
    assert patcher.main(("verify", str(target))) == 0

    # apply again is a no-op (idempotent) and keeps the file patched.
    assert patcher.main(("apply", str(target))) == 0
    assert patcher.verify_source(target.read_text(encoding="utf-8"))

    # revert restores the exact original bytes.
    assert patcher.main(("revert", str(target))) == 0
    assert target.read_text(encoding="utf-8") == FIXTURE


def test_verify_fails_on_unpatched_file(tmp_path: Path) -> None:
    target = tmp_path / "run.py"
    _ = target.write_text(FIXTURE, encoding="utf-8")
    assert patcher.main(("verify", str(target))) == 3
