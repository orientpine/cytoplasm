"""Hermes one-shot invocation for W5-3 report drafts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

if __package__ in (None, ""):
    from report_sensitivity import Route
else:
    from .report_sensitivity import Route


class LlmInvocationError(RuntimeError):
    pass


def _litellm_key() -> str:
    key = os.environ.get("LITELLM_AGENT_KEY", "")
    if key:
        return key
    try:
        lines = (Path.home() / ".env.secrets").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("LITELLM_AGENT_KEY="):
            return line.partition("=")[2].strip()
    raise LlmInvocationError("LITELLM_AGENT_KEY is unavailable")


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
    """Call only the pre-approved provider through the inert Hermes todo toolset."""
    _record_route(route)
    environment = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
    if route.provider == "custom:litellm":
        environment["LITELLM_AGENT_KEY"] = _litellm_key()
    try:
        completed = subprocess.run(
            ["hermes", "-z", prompt, "--provider", route.provider, "-m", route.model, "-t", "todo"],
            cwd=Path.home(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LlmInvocationError(error.__class__.__name__) from error
    if completed.returncode != 0 or not completed.stdout.strip():
        raise LlmInvocationError(f"provider rc={completed.returncode}")
    return completed.stdout.strip()
