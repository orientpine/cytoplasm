"""Fail-closed Codex OAuth routing for private document-type extraction and drafting.

Every call goes through the shared client in ``automation.codex_llm``: Codex OAuth
(provider ``openai-codex``) is pinned as the primary in argv, and Hermes may answer
from the account's configured ``fallback_providers`` chain when Codex cannot (owner
decision 2026-09-22). The routing gate proves the resolved route IS that shared
client with the Codex primary pinned, argv included, and refuses before transport
otherwise — a completer that names any other primary never receives the document.
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
_BINARY_ENV: Final = "AUTOPHAGY_HERMES_BIN"
_RELEASE_ROOT: Final = "/srv/autophagy-agent-current"


class PatentRoutingError(RuntimeError):
    """The resolved route is not the shared client with Codex pinned, so nothing is sent."""


class LlmCallError(RuntimeError):
    """A private one-shot model call did not return a usable response."""


def _log_path() -> Path:
    return Path(os.environ.get("DOCTYPE_LLM_LOG", "~/.hermes/doctype/logs/llm-calls.jsonl")).expanduser()


def _log_call(
    *,
    provider: str,
    model: str,
    served_provider: str,
    served_model: str,
    purpose: str,
    sensitive: bool,
    opaque_id: str,
) -> None:
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
        "served_model": served_model,
        "served_provider": served_provider,
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
    pinned = "--provider" in argv and argv[argv.index("--provider") + 1 :][:1] == [CODEX_PROVIDER]
    if codex.PROVIDER != CODEX_PROVIDER or not pinned:
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
        served = client.complete_served(prompt)
    except codex.CodexError as error:
        raise LlmCallError(f"Codex one-shot failed: {error}") from None
    _log_call(
        provider=CODEX_PROVIDER,
        model=CODEX_MODEL,
        served_provider=served.provider,
        served_model=served.model,
        purpose=purpose,
        sensitive=sensitive,
        opaque_id=opaque_id,
    )
    return served.text
