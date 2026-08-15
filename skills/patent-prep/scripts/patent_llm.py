"""One-shot Codex-only dispatch for patent-prep drafts."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .patent_routing import PatentCall, plan_patent_call


class LlmInvocationError(RuntimeError):
    """Hermes did not produce a usable Codex draft."""


@dataclass(frozen=True, slots=True)
class InvocationResult:
    """The minimal subprocess result contract used by production and tests."""

    returncode: int
    stdout: str


@dataclass(frozen=True, slots=True)
class DraftResponse:
    """A private draft plus its enforced non-GLM dispatch plan."""

    text: str
    call: PatentCall


Invoke = Callable[[tuple[str, ...]], InvocationResult]


def _record_call(call: PatentCall) -> None:
    """Record routing facts only; prompts and completions never enter this log."""
    directory = Path(os.environ.get("PATENT_LLM_LOG_ROOT", "~/.hermes/patent-prep/logs")).expanduser()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    record = {
        "run_id": os.environ.get("PATENT_RUN_ID", "unspecified"),
        "provider": call.provider,
        "model": call.model,
        "tags": list(call.tags),
        "tag_auto_attached": call.tag_auto_attached,
    }
    path = directory / "llm-calls.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(json.dumps(record, sort_keys=True) + "\n")
    path.chmod(0o600)


def _invoke(command: tuple[str, ...]) -> InvocationResult:
    """Run Hermes in the home directory, bypassing the GLM default provider."""
    environment = {
        **os.environ,
        "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}",
        "PATENT_SENSITIVE_TAG": "patent-sensitive",
    }
    try:
        completed = subprocess.run(
            command,
            cwd=Path.home(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LlmInvocationError(error.__class__.__name__) from error
    return InvocationResult(completed.returncode, completed.stdout)


def generate_draft(
    prompt: str, requested_tags: tuple[str, ...] = (), invoke: Invoke | None = None
) -> DraftResponse:
    """Attach the patent tag before one hard-coded Codex-only Hermes call."""
    call = plan_patent_call(requested_tags)
    _record_call(call)
    command = ("hermes", "-z", prompt, "--provider", call.provider, "-m", call.model, "-t", "todo")
    result = (invoke or _invoke)(command)
    if result.returncode != 0 or not result.stdout.strip():
        raise LlmInvocationError(f"codex rc={result.returncode}")
    return DraftResponse(result.stdout.strip(), call)
