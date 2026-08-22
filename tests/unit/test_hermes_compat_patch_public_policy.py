"""Tests for the public Discord message-policy compatibility patches.

Two appliers are covered: ``patch_public_message_policy.py`` (gateway/run.py)
and ``patch_discord_public_approval.py`` (the Discord adapter). Both are
exact-preimage patches, so the fixtures below embed the real v0.20.3 anchors
byte-for-byte — if the vendored source drifts, the on-node apply refuses and
these fixtures are what pin the strings the appliers depend on.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_HC = Path(__file__).resolve().parents[2] / "automation" / "hermes_compat"


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _HC / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patcher = _load("patch_public_message_policy", "patch_public_message_policy.py")
adapter_patcher = _load("patch_discord_public_approval", "patch_discord_public_approval.py")


# A syntactically valid stand-in embedding every real anchor byte-for-byte.
RUN_FIXTURE = '''\
import logging
import os


logger = logging.getLogger(__name__)


class TurnRunner:
    def _status_callback_sync(self, event_type: str, message: str) -> None:
        ctx = self._ctx
        if not ctx._status_adapter or not ctx._run_still_current():
            return
        _send_status(ctx, event_type, message)

    def run_sync(self):
        ctx = self._ctx
        _streaming_enabled = bool(ctx.streaming)
        _want_stream_deltas = _streaming_enabled
        _want_interim_messages = ctx.interim_assistant_messages_enabled
        return _want_stream_deltas, _want_interim_messages

    def _install_approval_callback(self, ctx):
        def _approval_callback(approval_data):
            cmd = approval_data.get("command", "")
            desc = approval_data.get("description", "dangerous command")

            cmd = _redact_approval_command(cmd)

            # Prefer button-based approval when the adapter supports it.
            return cmd, desc

        return _approval_callback


class GatewayRunner:
    async def _watch_process(self, watcher):
        session_id = watcher["session_id"]
        interval = watcher["check_interval"]
        platform_name = watcher.get("platform", "")
        chat_id = watcher.get("chat_id", "")
        agent_notify = watcher.get("notify_on_complete", False)
        notify_mode = self._load_background_notifications_mode()

        logger.debug("Process watcher started: %s (every %ss, notify=%s, agent_notify=%s)",
                      session_id, interval, notify_mode, agent_notify)

        while True:
            session = self._registry.get(session_id)
            if session.exited:
                new_output = session.output_buffer
                if True:
                    if notify_mode == "concise":
                        _cmd_disp = _redact_gateway_user_facing_secrets(
                            getattr(session, "command", "") or ""
                        )
                        _started = getattr(session, "started_at", None)
                        _dur = None
                        if isinstance(_started, (int, float)):
                            _dur = max(0.0, time.time() - _started)
                        message_text = _format_concise_process_notification(
                            session_id,
                            _cmd_disp,
                            session.exit_code,
                            new_output,
                            duration_seconds=_dur,
                        )
                    else:
                        message_text = (
                            f"[Background process {session_id} finished with exit code {session.exit_code}~ "
                            f"Here's the final output:\\n{new_output}]"
                        )
                    return message_text
            break

    def _run_agent(self, source, user_config, platform_key, progress_mode):
        def _display_surface_mode(
            setting: str,
            *,
            default: bool = False,
            allow_generic: bool = False,
        ) -> str:
            """Return off|raw|generic for a gateway visibility surface."""
            value = resolve_display_setting(user_config, platform_key, setting, default)
            if isinstance(value, str) and value.strip().lower() == "generic":
                return "generic" if allow_generic else "off"
            return "raw" if bool(value) else "off"

        # Disable tool progress for webhooks - they don't support message editing,
        # so each progress line would be sent as a separate message.
        from gateway.config import Platform
        tool_progress_enabled = progress_mode not in {"off", "log"} and source.platform != Platform.WEBHOOK
        interim_assistant_messages_mode = _display_surface_mode(
            "interim_assistant_messages",
            default=True,
        )
        return tool_progress_enabled, interim_assistant_messages_mode
'''


ADAPTER_FIXTURE = '''\
import logging

logger = logging.getLogger(__name__)


class DiscordAdapter:
    async def send_exec_approval(
        self, chat_id, command, session_key, description="dangerous command",
        metadata=None,
    ):
        try:
            target_id = chat_id
            if metadata and metadata.get("thread_id"):
                target_id = metadata["thread_id"]

            channel = self._client.get_channel(int(target_id))
            if not channel:
                channel = await self._client.fetch_channel(int(target_id))

            # Keep the approval request self-contained in plain message content.
            content = f"Requested command: {command} Reason: {description}"
            return content
        except Exception:
            return None
'''


# --------------------------------------------------------------------------
# gateway/run.py applier
# --------------------------------------------------------------------------


def test_patch_applies_every_seam_and_compiles() -> None:
    patched = patcher.patch_source(RUN_FIXTURE, filename="run.py")
    assert patcher.is_patched(patched)
    assert patcher.verify_source(patched)
    compile(patched, "run.py", "exec")


def test_every_declared_seam_is_present_in_the_result() -> None:
    patched = patcher.patch_source(RUN_FIXTURE, filename="run.py")
    for name, _, postimage in patcher._MODS:
        assert postimage in patched, name


def test_helpers_are_injected_before_their_first_use() -> None:
    patched = patcher.patch_source(RUN_FIXTURE, filename="run.py")
    definition = patched.index("def _hermes_pubpolicy_allows(")
    for call in (
        "_hermes_pubpolicy_allows(\n                    source.platform",
        '_hermes_pubpolicy_allows(\n            ctx.source.platform, getattr(ctx.source, "chat_type", None), "internal_status"',
    ):
        assert definition < patched.index(call)


def test_display_surface_resolver_is_patched_not_its_call_sites() -> None:
    # Patching the resolver is what makes a display surface upstream adds later
    # fail closed without a new patch, so pin that shape.
    patched = patcher.patch_source(RUN_FIXTURE, filename="run.py")
    assert patched.count("_pubpolicy_kind = _hermes_pubpolicy_display_kind(setting)") == 1
    # The individual display-surface call sites stay untouched — they inherit the
    # verdict from the resolver rather than each carrying their own check.
    assert 'interim_assistant_messages_mode = _display_surface_mode(\n            "interim_assistant_messages",\n            default=True,\n        )' in patched


def test_agent_notify_synth_text_is_left_alone() -> None:
    # Deliberate deviation from the original: the agent-notify branch wakes the
    # agent, it does not deliver to chat. Blanking it would blind the agent.
    patched = patcher.patch_source(RUN_FIXTURE, filename="run.py")
    assert "synth_text = _hermes_pubpolicy_completion_text" not in patched


def test_patch_is_idempotent() -> None:
    once = patcher.patch_source(RUN_FIXTURE, filename="run.py")
    twice = patcher.patch_source(once, filename="run.py")
    assert once == twice


@pytest.mark.parametrize(
    "broken",
    [
        RUN_FIXTURE.replace("logger = logging.getLogger(__name__)", "log = logging.getLogger(__name__)"),
        RUN_FIXTURE.replace("value = resolve_display_setting(", "value = resolve_setting("),
        RUN_FIXTURE.replace("tool_progress_enabled = progress_mode", "tp_enabled = progress_mode"),
        RUN_FIXTURE.replace("def _status_callback_sync", "def _status_cb_sync"),
        RUN_FIXTURE.replace("_want_stream_deltas = _streaming_enabled", "_want_stream_deltas = _on"),
        RUN_FIXTURE.replace("cmd = _redact_approval_command(cmd)", "cmd = _redact(cmd)"),
        RUN_FIXTURE.replace("notify_mode = self._load_background_notifications_mode()", "notify_mode = 'all'"),
        RUN_FIXTURE.replace('if notify_mode == "concise":', 'if notify_mode == "short":'),
    ],
)
def test_any_missing_preimage_refuses(broken: str) -> None:
    with pytest.raises(patcher.PreimageError):
        patcher.patch_source(broken, filename="run.py")


def test_duplicate_anchor_refuses() -> None:
    with pytest.raises(patcher.PreimageError):
        patcher.patch_source(RUN_FIXTURE + "\n" + RUN_FIXTURE, filename="run.py")


def test_apply_verify_revert_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "run.py"
    _ = target.write_text(RUN_FIXTURE, encoding="utf-8")

    assert patcher.main(("apply", str(target))) == 0
    backup = target.with_name("run.py" + patcher.BACKUP_SUFFIX)
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == RUN_FIXTURE
    assert patcher.main(("verify", str(target))) == 0
    # Re-apply is a no-op and keeps the file patched.
    assert patcher.main(("apply", str(target))) == 0
    assert patcher.verify_source(target.read_text(encoding="utf-8"))
    assert patcher.main(("revert", str(target))) == 0
    assert target.read_text(encoding="utf-8") == RUN_FIXTURE


def test_verify_fails_on_unpatched_file(tmp_path: Path) -> None:
    target = tmp_path / "run.py"
    _ = target.write_text(RUN_FIXTURE, encoding="utf-8")
    assert patcher.main(("verify", str(target))) == 3


# --------------------------------------------------------------------------
# The injected vendor-side helpers, executed
# --------------------------------------------------------------------------


def _exec_injected_helpers(carrier_available: bool) -> dict[str, object]:
    """Run the injected module-scope block and hand back its namespace."""
    namespace: dict[str, object] = {"__name__": "hermes_gateway_run_stub"}
    exec("import logging\nimport os\n", namespace)  # noqa: S102 - patch output under test
    exec(patcher._ANCHOR_POST, namespace)  # noqa: S102 - patch output under test
    if carrier_available:
        # On the node the bootstrap merges our package into `automation`; here the
        # repo root is already importable, so a stub bootstrap is enough.
        sys.modules.setdefault("hermes_compat_boot", ModuleType("hermes_compat_boot"))
    else:
        sys.modules.pop("hermes_compat_boot", None)
    return namespace


class _StubPlatform:
    def __init__(self, value: str) -> None:
        self.value = value


class _StubSource:
    platform = _StubPlatform("discord")
    chat_type = "channel"
    chat_id = "149001"
    thread_id = "149002"


def test_injected_helpers_use_the_carrier_policy_when_available() -> None:
    namespace = _exec_injected_helpers(carrier_available=True)
    allows = namespace["_hermes_pubpolicy_allows"]
    public = namespace["_hermes_pubpolicy_public_surface"]
    display_kind = namespace["_hermes_pubpolicy_display_kind"]

    assert public(_StubPlatform("discord"), "channel") is True
    assert public(_StubPlatform("discord"), "dm") is False
    assert allows(_StubPlatform("discord"), "channel", "tool_progress") is False
    assert allows(_StubPlatform("discord"), "channel", "final_result") is True
    assert allows(_StubPlatform("discord"), "dm", "tool_progress") is True
    assert display_kind("thinking_progress") == "reasoning"


def test_injected_helpers_fail_closed_without_the_carrier() -> None:
    namespace = _exec_injected_helpers(carrier_available=False)
    allows = namespace["_hermes_pubpolicy_allows"]
    public = namespace["_hermes_pubpolicy_public_surface"]

    # Public Discord: deny even a kind the allowlist would normally permit —
    # the seams only ever ask about internal telemetry, so deny is correct.
    assert public(_StubPlatform("discord"), "thread") is True
    assert allows(_StubPlatform("discord"), "thread", "tool_progress") is False
    # DMs and other platforms keep working.
    assert allows(_StubPlatform("discord"), "dm", "tool_progress") is True
    assert allows(_StubPlatform("slack"), "channel", "tool_progress") is True


def test_injected_completion_text_is_opaque_in_both_modes() -> None:
    for available in (True, False):
        namespace = _exec_injected_helpers(carrier_available=available)
        text = namespace["_hermes_pubpolicy_completion_text"]("bg_7f21", 2)
        assert "bg_7f21" in text
        assert "exit code 2" in text
        assert "output" not in text


def test_injected_audit_writes_a_content_free_line() -> None:
    namespace = _exec_injected_helpers(carrier_available=True)
    records: list[str] = []

    class _Logger:
        def info(self, msg: str, /, *args: object) -> None:
            records.append(msg % args)

    namespace["logger"] = _Logger()
    namespace["_hermes_pubpolicy_audit"]("tool_progress", _StubSource())
    assert len(records) == 1
    assert "suppressed" in records[0]
    assert "tool_progress" in records[0]
    assert "149001" in records[0]


# --------------------------------------------------------------------------
# discord adapter applier
# --------------------------------------------------------------------------


def test_adapter_patch_applies_and_compiles() -> None:
    patched = adapter_patcher.patch_source(ADAPTER_FIXTURE, filename="adapter.py")
    assert adapter_patcher.is_patched(patched)
    assert adapter_patcher.verify_source(patched)
    compile(patched, "adapter.py", "exec")


def test_adapter_patch_withholds_before_rendering_the_content() -> None:
    patched = adapter_patcher.patch_source(ADAPTER_FIXTURE, filename="adapter.py")
    withheld_at = patched.index('command = "[operation details withheld on public Discord]"')
    render_at = patched.index("content = f\"Requested command: {command}")
    assert withheld_at < render_at


def test_adapter_patch_treats_unknown_metadata_as_public() -> None:
    patched = adapter_patcher.patch_source(ADAPTER_FIXTURE, filename="adapter.py")
    # No chat_type -> decided by the resolved channel's guild, never by assuming DM.
    assert '_pubapproval_public = getattr(channel, "guild", None) is not None' in patched
    assert '_pubapproval_public = _pubapproval_chat_type != "dm"' in patched


def test_adapter_patch_does_not_import_the_carrier() -> None:
    # This path must keep working while the carrier is half-deployed.
    patched = adapter_patcher.patch_source(ADAPTER_FIXTURE, filename="adapter.py")
    assert "hermes_compat_boot" not in patched
    assert "public_message_policy" not in patched


def test_adapter_patch_is_idempotent() -> None:
    once = adapter_patcher.patch_source(ADAPTER_FIXTURE, filename="adapter.py")
    assert adapter_patcher.patch_source(once, filename="adapter.py") == once


def test_adapter_missing_preimage_refuses() -> None:
    broken = ADAPTER_FIXTURE.replace("channel = self._client.get_channel(", "channel = self._get(")
    with pytest.raises(adapter_patcher.PreimageError):
        adapter_patcher.patch_source(broken, filename="adapter.py")


def test_adapter_apply_verify_revert_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "adapter.py"
    _ = target.write_text(ADAPTER_FIXTURE, encoding="utf-8")
    assert adapter_patcher.main(("apply", str(target))) == 0
    assert adapter_patcher.main(("verify", str(target))) == 0
    assert adapter_patcher.main(("revert", str(target))) == 0
    assert target.read_text(encoding="utf-8") == ADAPTER_FIXTURE
