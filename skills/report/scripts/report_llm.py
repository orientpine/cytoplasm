"""Codex OAuth invocation for W5-3 report drafts.

Codex OAuth is the pinned primary and the account's Hermes `fallback_providers`
chain may answer instead: the draft is written by the shared `automation.codex_llm`
client or not at all, and the log records the provider/model that actually answered. The client is imported lazily so a
deployed skill copy does not import the repository at module load; an
ImportError refuses the draft instead of calling a model on its own.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final

LLM_TIMEOUT: Final = 600.0
_REPO_ROOT_ENV: Final = "AUTOPHAGY_REPO_ROOT"
_RELEASE_ROOT: Final = Path("/srv/autophagy-agent-current")


class LlmInvocationError(RuntimeError):
    pass


def _repo_root() -> Path:
    """Resolve the repository without importing it — deploy copies live outside it."""
    override = os.environ.get(_REPO_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    for parent in Path(__file__).resolve().parents:
        if (parent / "automation" / "skill_mount.py").is_file():
            return parent
    return _RELEASE_ROOT


def _record_route(served_provider: str, served_model: str) -> None:
    directory = Path.home() / ".hermes" / "report" / "logs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / "llm-calls.jsonl"
    record = {
        "run_id": os.environ.get("REPORT_RUN_ID", "unspecified"),
        "provider": "openai-codex",
        "model": "hermes-config",
        "served_model": served_model,
        "served_provider": served_provider,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    path.chmod(0o600)


def generate(prompt: str) -> str:
    """Call the shared account-model client."""
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from automation.codex_llm import UNKNOWN, CodexClient, CodexError  # noqa: PLC0415
    except ImportError as error:
        name = error.__class__.__name__
        raise LlmInvocationError(f"shared Codex client unavailable ({name})") from error
    try:
        client = CodexClient.from_environment(timeout=LLM_TIMEOUT)
        served = client.complete_served(prompt)
    except CodexError as error:
        _record_route(UNKNOWN, UNKNOWN)
        raise LlmInvocationError(str(error)) from error
    _record_route(served.provider, served.model)
    return served.text
