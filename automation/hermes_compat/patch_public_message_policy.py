#!/usr/bin/env python3
"""Idempotent, exact-preimage patch: public Discord visibility policy in run.py.

Root ticket t_db6a60e8 — stop exposing the agent's internal work process on
Discord surfaces that are not a 1:1 DM. The policy itself lives in
``automation/hermes_compat/public_message_policy.py`` (shipped as a runtime dep
to ``~/.hermes/hermes-compat/``); this module only injects the seams that call
it, so the decision table can be reviewed and unit-tested without reading
vendored gateway source.

Preimages are taken from the vendored source **actually running on the node**
(Hermes v0.20.3, head ``a3995f8a``) — read read-only from both the agent and the
peer install — not from the v0.18.2 workspace clone the original implementation
was written against (v0.18.2 ``_run_agent`` was one giant closure; v0.20.3 split
the turn body into ``TurnRunner``/``TurnContext``, so five of these seams moved
class).

Seams
-----
MOD0  module scope    — policy helpers (bootstrap import + fail-closed fallback).
MOD1  ``_display_surface_mode`` — the single resolver for every gateway display
      surface (interim assistant messages, thinking relay, long-running
      heartbeats, and anything upstream adds later). Patching the *resolver*
      instead of its three call sites is deliberate: a display surface added
      upstream tomorrow is denied on public Discord without a new patch.
MOD2  ``tool_progress_enabled`` — progress_mode is resolved separately (env +
      legacy overrides), so it needs its own seam.
MOD3  ``TurnRunner._status_callback_sync`` — context-pressure/retry/compaction
      status callbacks.
MOD4  ``_want_stream_deltas`` — token streaming drafts.
MOD5  approval prompt — the request stays public (the owner must act on it),
      the raw command and reason do not.
MOD6  ``_watch_process`` head — downgrade notify ``all`` -> ``result`` so
      periodic raw process output stops, completion delivery survives.
MOD7  ``_watch_process`` completion send — replace the raw-output text (both the
      ``concise`` and the default rendering) with an opaque completion notice.

Deliberate deviation from the original workspace implementation
---------------------------------------------------------------
The original also replaced ``synth_text`` on the *agent-notify* branch. That
branch does not deliver to the chat: ``_enqueue_process_completion_notification``
-> ``_deliver_completion_notification`` -> ``_inject_watch_notification`` builds a
``MessageEvent(internal=True)`` (or ``deliver_wake``) that wakes the **agent**.
Blanking it would blind the agent to its own background job while adding no
privacy: whatever the agent then says is a ``final_result``, which is
allowlisted and still passes the normal redaction path. The user-visible
guarantee (no raw process output on a public Discord surface) is kept by MOD6 +
MOD7, which sit on the direct ``adapter.send`` path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

MARKER: Final = "_hermes_pubpolicy_done"
BACKUP_SUFFIX: Final = ".autophagy-orig"

# --------------------------------------------------------------------------
# MOD0 — module-scope policy helpers.
#
# Fail-closed by construction: if the carrier module cannot be imported (not
# deployed, half-deployed, broken), the fallback still recognises a public
# Discord surface and denies every kind the injected seams ask about. Every
# injected call site names an internal-telemetry kind, so "deny" is the correct
# degraded answer; DMs and non-Discord platforms are unaffected either way.
# --------------------------------------------------------------------------
_ANCHOR_PRE: Final = "\n\nlogger = logging.getLogger(__name__)\n"
_ANCHOR_POST: Final = (
    "\n\nlogger = logging.getLogger(__name__)\n"
    "\n"
    "# autophagy hermes_compat (_hermes_pubpolicy_done): public Discord visibility policy.\n"
    "# Decision table lives in automation/hermes_compat/public_message_policy.py; these\n"
    "# helpers are the only vendor-side surface, and they fail CLOSED (suppress) when the\n"
    "# carrier module is unavailable.\n"
    "_hermes_pubpolicy_cache = []\n"
    "\n"
    "\n"
    "def _hermes_pubpolicy():\n"
    "    if _hermes_pubpolicy_cache:\n"
    "        return _hermes_pubpolicy_cache[0]\n"
    "    module = None\n"
    "    try:\n"
    "        import sys as _pp_sys\n"
    '        _pp_dir = os.path.expanduser("~/.hermes/hermes-compat")\n'
    "        if _pp_dir not in _pp_sys.path:\n"
    "            _pp_sys.path.insert(0, _pp_dir)\n"
    "        import hermes_compat_boot  # noqa: F401\n"
    "        from automation.hermes_compat import public_message_policy as module\n"
    "    except Exception:\n"
    "        module = None\n"
    "    _hermes_pubpolicy_cache.append(module)\n"
    "    return module\n"
    "\n"
    "\n"
    "def _hermes_pubpolicy_public_surface(platform, chat_type):\n"
    '    """True when the route is Discord and not a 1:1 DM."""\n'
    "    policy = _hermes_pubpolicy()\n"
    "    if policy is not None:\n"
    "        try:\n"
    "            return bool(policy.is_public_discord_surface(platform, chat_type))\n"
    "        except Exception:\n"
    "            pass\n"
    '    name = str(getattr(platform, "value", platform) or "").strip().lower()\n'
    '    return name == "discord" and str(chat_type or "").strip().lower() != "dm"\n'
    "\n"
    "\n"
    "def _hermes_pubpolicy_allows(platform, chat_type, kind):\n"
    '    """Allowlist check; without the carrier module every asked-about kind is denied."""\n'
    "    policy = _hermes_pubpolicy()\n"
    "    if policy is not None:\n"
    "        try:\n"
    "            return bool(policy.event_allowed_on_surface(platform, chat_type, kind))\n"
    "        except Exception:\n"
    "            pass\n"
    "    return not _hermes_pubpolicy_public_surface(platform, chat_type)\n"
    "\n"
    "\n"
    "def _hermes_pubpolicy_display_kind(setting):\n"
    '    """Name the event kind a gateway display-surface setting renders."""\n'
    "    policy = _hermes_pubpolicy()\n"
    "    if policy is not None:\n"
    "        try:\n"
    "            return policy.display_surface_kind(setting).value\n"
    "        except Exception:\n"
    "            pass\n"
    '    return "unknown"\n'
    "\n"
    "\n"
    "def _hermes_pubpolicy_audit(kind, source):\n"
    '    """Content-free suppression breadcrumb in the existing redacted gateway log."""\n'
    "    policy = _hermes_pubpolicy()\n"
    "    if policy is not None:\n"
    "        try:\n"
    "            policy.audit_suppressed_event(logger, kind, source)\n"
    "            return\n"
    "        except Exception:\n"
    "            pass\n"
    "    logger.info(\n"
    '        "Public Discord delivery suppressed: event=%s chat_id=%s",\n'
    "        str(kind)[:48],\n"
    '        str(getattr(source, "chat_id", "") or "")[:96],\n'
    "    )\n"
    "\n"
    "\n"
    "def _hermes_pubpolicy_completion_text(session_id, exit_code):\n"
    '    """Opaque completion notice: handle, state, exit code — no command or output."""\n'
    "    policy = _hermes_pubpolicy()\n"
    "    if policy is not None:\n"
    "        try:\n"
    "            return str(policy.public_background_completion_text(session_id, exit_code))\n"
    "        except Exception:\n"
    "            pass\n"
    '    label = (str(session_id or "background task").strip() or "background task")[:80]\n'
    '    if exit_code in {0, "0"}:\n'
    '        return f"\N{WHITE HEAVY CHECK MARK} Background task `{label}` completed successfully."\n'
    "    if exit_code is None:\n"
    '        return f"\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16} Background task `{label}` completed."\n'
    '    return f"\N{WARNING SIGN}\N{VARIATION SELECTOR-16} Background task `{label}` failed (exit code {exit_code})."\n'
)

# --------------------------------------------------------------------------
# MOD1 — _display_surface_mode: one resolver, every display surface.
# --------------------------------------------------------------------------
_MOD1_PRE: Final = (
    "            value = resolve_display_setting(user_config, platform_key, setting, default)\n"
    '            if isinstance(value, str) and value.strip().lower() == "generic":\n'
    '                return "generic" if allow_generic else "off"\n'
    '            return "raw" if bool(value) else "off"\n'
)
_MOD1_POST: Final = (
    "            value = resolve_display_setting(user_config, platform_key, setting, default)\n"
    '            if isinstance(value, str) and value.strip().lower() == "generic":\n'
    '                _pubpolicy_mode = "generic" if allow_generic else "off"\n'
    "            else:\n"
    '                _pubpolicy_mode = "raw" if bool(value) else "off"\n'
    "            # autophagy hermes_compat (_hermes_pubpolicy_done): every surface resolved\n"
    "            # here is internal turn telemetry. On a public Discord surface it is denied\n"
    "            # unless its event kind is user-facing; a surface upstream adds later maps\n"
    "            # to no kind, so it is denied too (fail-closed, no new patch needed).\n"
    '            if _pubpolicy_mode != "off":\n'
    "                _pubpolicy_kind = _hermes_pubpolicy_display_kind(setting)\n"
    "                if not _hermes_pubpolicy_allows(\n"
    '                    source.platform, getattr(source, "chat_type", None), _pubpolicy_kind\n'
    "                ):\n"
    "                    _hermes_pubpolicy_audit(_pubpolicy_kind, source)\n"
    '                    return "off"\n'
    "            return _pubpolicy_mode\n"
)

# --------------------------------------------------------------------------
# MOD2 — tool progress (raw tool names and arguments).
# --------------------------------------------------------------------------
_MOD2_PRE: Final = (
    "        # Disable tool progress for webhooks - they don't support message editing,\n"
    "        # so each progress line would be sent as a separate message.\n"
    "        from gateway.config import Platform\n"
    '        tool_progress_enabled = progress_mode not in {"off", "log"} and source.platform != Platform.WEBHOOK\n'
)
_MOD2_POST: Final = (
    "        # Disable tool progress for webhooks - they don't support message editing,\n"
    "        # so each progress line would be sent as a separate message.\n"
    "        from gateway.config import Platform\n"
    "        # autophagy hermes_compat (_hermes_pubpolicy_done): tool progress carries tool\n"
    "        # names and arguments verbatim — internal telemetry, never public.\n"
    "        tool_progress_enabled = (\n"
    '            progress_mode not in {"off", "log"}\n'
    "            and source.platform != Platform.WEBHOOK\n"
    "            and _hermes_pubpolicy_allows(\n"
    '                source.platform, getattr(source, "chat_type", None), "tool_progress"\n'
    "            )\n"
    "        )\n"
    '        if progress_mode not in {"off", "log"} and not tool_progress_enabled:\n'
    "            if _hermes_pubpolicy_public_surface(\n"
    '                source.platform, getattr(source, "chat_type", None)\n'
    "            ):\n"
    '                _hermes_pubpolicy_audit("tool_progress", source)\n'
)

# --------------------------------------------------------------------------
# MOD3 — gateway status callbacks (context pressure, retries, compaction).
# --------------------------------------------------------------------------
_MOD3_PRE: Final = (
    "    def _status_callback_sync(self, event_type: str, message: str) -> None:\n"
    "        ctx = self._ctx\n"
    "        if not ctx._status_adapter or not ctx._run_still_current():\n"
    "            return\n"
)
_MOD3_POST: Final = (
    "    def _status_callback_sync(self, event_type: str, message: str) -> None:\n"
    "        ctx = self._ctx\n"
    "        if not ctx._status_adapter or not ctx._run_still_current():\n"
    "            return\n"
    "        # autophagy hermes_compat (_hermes_pubpolicy_done): gateway status callbacks\n"
    "        # describe the run's internal state, not an answer to the user.\n"
    "        if not _hermes_pubpolicy_allows(\n"
    '            ctx.source.platform, getattr(ctx.source, "chat_type", None), "internal_status"\n'
    "        ):\n"
    '            _hermes_pubpolicy_audit("internal_status", ctx.source)\n'
    "            return\n"
)

# --------------------------------------------------------------------------
# MOD4 — token streaming drafts.
# --------------------------------------------------------------------------
_MOD4_PRE: Final = (
    "        _want_stream_deltas = _streaming_enabled\n"
    "        _want_interim_messages = ctx.interim_assistant_messages_enabled\n"
)
_MOD4_POST: Final = (
    "        # autophagy hermes_compat (_hermes_pubpolicy_done): a streaming draft is not a\n"
    "        # final result — a public Discord surface receives the completed answer only.\n"
    "        _want_stream_deltas = _streaming_enabled and _hermes_pubpolicy_allows(\n"
    '            ctx.source.platform, getattr(ctx.source, "chat_type", None), "streaming_draft"\n'
    "        )\n"
    "        if _streaming_enabled and not _want_stream_deltas:\n"
    '            _hermes_pubpolicy_audit("streaming_draft", ctx.source)\n'
    "        _want_interim_messages = ctx.interim_assistant_messages_enabled\n"
)

# --------------------------------------------------------------------------
# MOD5 — approval prompt stays public, its command/reason do not.
# --------------------------------------------------------------------------
_MOD5_PRE: Final = (
    "            cmd = _redact_approval_command(cmd)\n"
    "\n"
    "            # Prefer button-based approval when the adapter supports it.\n"
)
_MOD5_POST: Final = (
    "            cmd = _redact_approval_command(cmd)\n"
    "            # autophagy hermes_compat (_hermes_pubpolicy_done): the approval request\n"
    "            # itself is allowlisted (the owner has to act on it), but the command and\n"
    "            # reason are tool arguments. The Discord adapter enforces the same rule for\n"
    "            # callers that reach send_exec_approval directly.\n"
    "            if _hermes_pubpolicy_public_surface(\n"
    '                ctx.source.platform, getattr(ctx.source, "chat_type", None)\n'
    "            ):\n"
    '                _hermes_pubpolicy_audit("approval_request", ctx.source)\n'
    '                cmd = "[operation details withheld on public Discord]"\n'
    '                desc = "A protected operation requires your approval."\n'
    "\n"
    "            # Prefer button-based approval when the adapter supports it.\n"
)

# --------------------------------------------------------------------------
# MOD6 — background watcher: stop periodic output, keep the completion.
# --------------------------------------------------------------------------
_MOD6_PRE: Final = (
    '        agent_notify = watcher.get("notify_on_complete", False)\n'
    "        notify_mode = self._load_background_notifications_mode()\n"
    "\n"
    '        logger.debug("Process watcher started: %s (every %ss, notify=%s, agent_notify=%s)",\n'
    "                      session_id, interval, notify_mode, agent_notify)\n"
)
_MOD6_POST: Final = (
    '        agent_notify = watcher.get("notify_on_complete", False)\n'
    "        notify_mode = self._load_background_notifications_mode()\n"
    "        # autophagy hermes_compat (_hermes_pubpolicy_done): periodic background-process\n"
    "        # output is internal telemetry. Completion delivery survives (all -> result);\n"
    "        # the raw output stays in the process registry and the session records.\n"
    "        _hermes_pubpolicy_watcher = _hermes_pubpolicy_public_surface(\n"
    '            platform_name, watcher.get("chat_type", "")\n'
    "        )\n"
    '        if _hermes_pubpolicy_watcher and notify_mode == "all":\n'
    "            logger.info(\n"
    '                "Public Discord delivery suppressed: event=%s process=%s",\n'
    '                "background_progress",\n'
    "                session_id,\n"
    "            )\n"
    '            notify_mode = "result"\n'
    "\n"
    '        logger.debug("Process watcher started: %s (every %ss, notify=%s, agent_notify=%s)",\n'
    "                      session_id, interval, notify_mode, agent_notify)\n"
)

# --------------------------------------------------------------------------
# MOD7 — the direct adapter.send of raw completion output.
# --------------------------------------------------------------------------
_MOD7_PRE: Final = (
    '                    if notify_mode == "concise":\n'
    "                        _cmd_disp = _redact_gateway_user_facing_secrets(\n"
    '                            getattr(session, "command", "") or ""\n'
    "                        )\n"
    '                        _started = getattr(session, "started_at", None)\n'
    "                        _dur = None\n"
    "                        if isinstance(_started, (int, float)):\n"
    "                            _dur = max(0.0, time.time() - _started)\n"
    "                        message_text = _format_concise_process_notification(\n"
    "                            session_id,\n"
    "                            _cmd_disp,\n"
    "                            session.exit_code,\n"
    "                            new_output,\n"
    "                            duration_seconds=_dur,\n"
    "                        )\n"
    "                    else:\n"
    "                        message_text = (\n"
    '                            f"[Background process {session_id} finished with exit code {session.exit_code}~ "\n'
    '                            f"Here\'s the final output:\\n{new_output}]"\n'
    "                        )\n"
)
_MOD7_POST: Final = (
    _MOD7_PRE
    + "                    # autophagy hermes_compat (_hermes_pubpolicy_done): both renderings\n"
    "                    # above embed the command and/or the process output. On a public\n"
    "                    # Discord surface only the completion fact is delivered.\n"
    "                    if _hermes_pubpolicy_watcher:\n"
    "                        logger.info(\n"
    '                            "Public Discord delivery suppressed: event=%s process=%s",\n'
    '                            "background_output",\n'
    "                            session_id,\n"
    "                        )\n"
    "                        message_text = _hermes_pubpolicy_completion_text(\n"
    "                            session_id, session.exit_code\n"
    "                        )\n"
)

_MODS: Final = (
    ("MOD0", _ANCHOR_PRE, _ANCHOR_POST),
    ("MOD1", _MOD1_PRE, _MOD1_POST),
    ("MOD2", _MOD2_PRE, _MOD2_POST),
    ("MOD3", _MOD3_PRE, _MOD3_POST),
    ("MOD4", _MOD4_PRE, _MOD4_POST),
    ("MOD5", _MOD5_PRE, _MOD5_POST),
    ("MOD6", _MOD6_PRE, _MOD6_POST),
    ("MOD7", _MOD7_PRE, _MOD7_POST),
)


class PreimageError(RuntimeError):
    """Raised when target source differs from an expected exact preimage."""


def is_patched(src: str) -> bool:
    """Return True only when every modification is present."""
    return MARKER in src and all(post in src for _, _, post in _MODS)


def patch_source(src: str, *, filename: str = "<run.py>") -> str:
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
    """Return True when every policy seam is installed and wired to the carrier."""
    return is_patched(src) and all(
        needle in src
        for needle in (
            "from automation.hermes_compat import public_message_policy as module",
            "def _hermes_pubpolicy_public_surface(platform, chat_type):",
            "def _hermes_pubpolicy_allows(platform, chat_type, kind):",
            "_pubpolicy_kind = _hermes_pubpolicy_display_kind(setting)",
            '"tool_progress"\n',
            '"internal_status"\n',
            '"streaming_draft"\n',
            'cmd = "[operation details withheld on public Discord]"',
            '_hermes_pubpolicy_watcher and notify_mode == "all"',
            "message_text = _hermes_pubpolicy_completion_text(",
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
    """Execute apply, verify, or revert against one vendored gateway/run.py."""
    match argv:
        case ("apply", target):
            return _apply(Path(target))
        case ("verify", target):
            return _verify(Path(target))
        case ("revert", target):
            return _revert(Path(target))
        case _:
            print(
                "usage: patch_public_message_policy.py apply|verify|revert <run.py>",
                file=sys.stderr,
            )
            return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(tuple(sys.argv[1:])))
    except PreimageError as exc:
        print(f"PREIMAGE-ERROR {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
