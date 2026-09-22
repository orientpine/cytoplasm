"""One-shot Codex OAuth LLM calls for private proposal drafting and review.

Every call goes through the one shared client (``automation.codex_llm``): Codex OAuth
pinned as the primary, the account's Hermes ``fallback_providers`` chain honored, no
client-side retry. A route that does not name the Codex OAuth tier — and a client that
cannot be imported — refuses the call. The log records the requested primary and the
provider/model that actually answered.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

CODEX_PROVIDER: Final = "openai-codex"
FINAL_REVIEW_MODEL: Final = "gpt-5.4"
_TIMEOUT_SECONDS: Final = 600.0
_RELEASE_ROOT: Final = "/srv/autophagy-agent-current"


class LlmInvocationError(RuntimeError):
    """The Codex OAuth tier did not return a usable one-shot response."""


def _log(  # noqa: PLR0913 - one masked routing line, all fields named
    stage: str, provider: str, model: str, sensitive: bool, served_provider: str, served_model: str
) -> None:
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
                    "served_model": served_model,
                    "served_provider": served_provider,
                },
                sort_keys=True,
            )
            + "\n"
        )
    path.chmod(0o600)


def _repo_root() -> Path:
    """Where the shared client lives; skills resolve it lazily, never at import time."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    for parent in Path(__file__).resolve().parents:
        if (parent / "automation" / "skill_mount.py").is_file():
            return parent
    return Path(_RELEASE_ROOT)


def _codex() -> ModuleType:
    """Import the shared Codex client; an unavailable client refuses, never falls back."""
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from automation import codex_llm  # noqa: PLC0415 - lazy repo-root import
    except ImportError as error:
        raise LlmInvocationError(
            f"Codex OAuth client unavailable: {error.__class__.__name__}"
        ) from None
    return codex_llm


def _run(stage: str, prompt: str, provider: str, model: str, sensitive: bool) -> str:
    """Refuse anything that is not the Codex OAuth tier, then make exactly one call."""
    if provider != CODEX_PROVIDER:
        raise LlmInvocationError(f"{stage} refused: {provider!r} is not the Codex OAuth tier")
    codex = _codex()
    try:
        served = codex.complete_served(prompt, model=model, timeout=_TIMEOUT_SECONDS)
    except codex.CodexError as error:
        _log(stage, CODEX_PROVIDER, model, sensitive, codex.UNKNOWN, codex.UNKNOWN)
        raise LlmInvocationError(f"{stage} failed: {error}") from None
    _log(stage, CODEX_PROVIDER, model, sensitive, served.provider, served.model)
    return served.text


def run_section_draft(prompt: str, provider: str, model: str, sensitive: bool) -> str:
    """Generate one section draft through the preselected Codex OAuth route."""
    return _run("section-draft", prompt, provider, model, sensitive)


def run_final_review(prompt: str) -> str:
    """Run exactly one required Codex OAuth final review invocation."""
    return _run("final-review", prompt, CODEX_PROVIDER, FINAL_REVIEW_MODEL, True)
