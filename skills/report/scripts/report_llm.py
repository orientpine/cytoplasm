"""Codex OAuth invocation for W5-3 report drafts.

One provider, one model, no fallback: the draft is written by the shared
`automation.codex_llm` client or not at all. The client is imported lazily so a
deployed skill copy does not import the repository at module load; an
ImportError refuses the draft instead of calling a model on its own.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final

if __package__ in (None, ""):
    from report_sensitivity import CODEX_PROVIDER, Route
else:
    from .report_sensitivity import CODEX_PROVIDER, Route

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


def _record_route(route: Route) -> None:
    directory = Path.home() / ".hermes" / "report" / "logs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / "llm-calls.jsonl"
    record = {
        "run_id": os.environ.get("REPORT_RUN_ID", "unspecified"),
        "provider": route.provider,
        "model": route.model,
        "sensitive": route.sensitive,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    path.chmod(0o600)


def generate(prompt: str, route: Route) -> str:
    """Call the pre-approved Codex OAuth route; refuse anything else (fail closed).

    The guard that used to keep patent-sensitive notes off the second tier now
    reads the other way round: only the Codex OAuth route may be called at all,
    so an unexpected route is refused before the prompt leaves this process.
    """
    if route.provider != CODEX_PROVIDER:
        raise LlmInvocationError(f"refused route {route.provider!r}; only {CODEX_PROVIDER} runs")
    _record_route(route)
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from automation.codex_llm import CodexClient, CodexError  # noqa: PLC0415
    except ImportError as error:
        name = error.__class__.__name__
        raise LlmInvocationError(f"shared Codex client unavailable ({name})") from error
    try:
        client = CodexClient.from_environment(timeout=LLM_TIMEOUT).with_model(route.model)
        return client.complete(prompt)
    except CodexError as error:
        raise LlmInvocationError(str(error)) from error
