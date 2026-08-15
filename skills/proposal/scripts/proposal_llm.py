"""One-shot Hermes LLM calls for private proposal drafting and review."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class LlmInvocationError(RuntimeError):
    """Hermes did not return a usable one-shot response."""


@dataclass(frozen=True, slots=True)
class InvocationResult:
    """Minimal subprocess result contract that unit tests can fake."""

    returncode: int
    stdout: str


Invoke = Callable[[tuple[str, ...]], InvocationResult]


def _log(stage: str, provider: str, model: str, sensitive: bool) -> None:
    directory = Path(os.environ.get("PROPOSAL_LLM_LOG_ROOT", "~/.hermes/proposal/logs")).expanduser()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / "llm-calls.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(
            json.dumps(
                {
                    "run_id": os.environ.get("PROPOSAL_RUN_ID", "unspecified"),
                    "stage": stage,
                    "provider": provider,
                    "model": model,
                    "sensitive": sensitive,
                },
                sort_keys=True,
            )
            + "\n"
        )
    path.chmod(0o600)


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


def _invoke(command: tuple[str, ...], *, litellm_key: str | None = None) -> InvocationResult:
    environment = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
    if litellm_key is not None:
        environment["LITELLM_AGENT_KEY"] = litellm_key
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


def _run(
    stage: str,
    prompt: str,
    provider: str,
    model: str,
    sensitive: bool,
    invoke: Invoke | None = None,
) -> str:
    _log(stage, provider, model, sensitive)
    command = ("hermes", "-z", prompt, "--provider", provider, "-m", model, "-t", "todo")
    if invoke is not None:
        result = invoke(command)
    else:
        key = _litellm_key() if provider == "custom:litellm" else None
        result = _invoke(command, litellm_key=key)
    if result.returncode != 0 or not result.stdout.strip():
        raise LlmInvocationError(f"{stage} rc={result.returncode}")
    return result.stdout.strip()


def run_section_draft(
    prompt: str, provider: str, model: str, sensitive: bool, invoke: Invoke | None = None
) -> str:
    """Generate one section draft through the preselected sensitivity route."""
    return _run("section-draft", prompt, provider, model, sensitive, invoke)


def run_final_review(prompt: str, invoke: Invoke | None = None) -> str:
    """Run exactly one required Codex/gpt-5.4 final review invocation."""
    return _run("final-review", prompt, "openai-codex", "gpt-5.4", True, invoke)
