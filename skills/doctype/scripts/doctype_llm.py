"""Fail-closed Codex OAuth routing for private document-type extraction and drafting.

There is one model tier: the shared client in ``automation.codex_llm`` (provider
``openai-codex``). The routing gate that used to keep sensitivity-gated text off a
second provider now proves the opposite property — that the resolved route IS the
pinned Codex OAuth tier, argv included — and refuses before transport otherwise.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Final


CODEX_PROVIDER: Final = "openai-codex"
CODEX_MODEL: Final = "gpt-5.4"
# Load bearing: without it Hermes reads the user config and may switch providers.
_IGNORE_USER_CONFIG: Final = "--ignore-user-config"
_BINARY_ENV: Final = "AUTOPHAGY_HERMES_BIN"
_RELEASE_ROOT: Final = "/srv/autophagy-agent-current"


class PatentRoutingError(RuntimeError):
    """The resolved route is not the pinned Codex OAuth tier, so nothing is sent."""


class LlmCallError(RuntimeError):
    """A private one-shot model call did not return a usable response."""


def _log_path() -> Path:
    return Path(os.environ.get("DOCTYPE_LLM_LOG", "~/.hermes/doctype/logs/llm-calls.jsonl")).expanduser()


def _log_call(*, provider: str, model: str, purpose: str, sensitive: bool, opaque_id: str) -> None:
    """Append only masked routing facts; prompt and completion bodies are forbidden."""
    path = _log_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    record = {
        "model": model,
        "opaque_id": opaque_id,
        "provider": provider,
        "purpose": purpose,
        "sensitive": sensitive,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _ = handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)


def _repo_root() -> Path:
    """Where the shared client lives; resolved lazily so the skill mount stays importable."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    for parent in Path(__file__).resolve().parents:
        if (parent / "automation" / "skill_mount.py").is_file():
            return parent
    return Path(_RELEASE_ROOT)


def _codex() -> ModuleType:
    """Import the shared Codex client; an unavailable client refuses the call."""
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from automation import codex_llm  # noqa: PLC0415 - lazy repo-root import
    except ImportError as error:
        raise LlmCallError(f"Codex OAuth client unavailable: {error.__class__.__name__}") from None
    return codex_llm


def _client_environment() -> dict[str, str]:
    """Keep the documented DOCTYPE_HERMES_BIN offline hook pointing at the shared client."""
    environment = dict(os.environ)
    stub = environment.get("DOCTYPE_HERMES_BIN", "").strip()
    if stub:
        environment[_BINARY_ENV] = stub
    return environment


def _codex_client(codex: ModuleType, timeout: float) -> Any:
    """Build the pinned client and prove the route before any document text moves."""
    try:
        client = codex.CodexClient.from_environment(_client_environment(), timeout=timeout)
    except codex.CodexError as error:
        raise LlmCallError(f"Codex OAuth tier unavailable: {error}") from None
    argv = client.argv("")
    if codex.PROVIDER != CODEX_PROVIDER or _IGNORE_USER_CONFIG not in argv:
        raise PatentRoutingError("routing gate: only the pinned Codex OAuth tier may receive this document")
    return client.with_model(CODEX_MODEL)


def call_codex(
    prompt: str,
    *,
    purpose: str = "unspecified",
    sensitive: bool = False,
    opaque_id: str = "-",
    timeout: float = 600.0,
) -> str:
    """Use the mandatory Codex OAuth tier and log only masked routing facts."""
    codex = _codex()
    client = _codex_client(codex, timeout)
    try:
        result = client.complete(prompt)
    except codex.CodexError as error:
        raise LlmCallError(f"Codex one-shot failed: {error}") from None
    _log_call(provider=CODEX_PROVIDER, model=CODEX_MODEL, purpose=purpose, sensitive=sensitive, opaque_id=opaque_id)
    return result
