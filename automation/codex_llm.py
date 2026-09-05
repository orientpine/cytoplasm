"""Single shared Codex OAuth LLM client — the only model call path in this repository.

Every automation, skill, cron and batch caller goes through :class:`CodexClient`.
The client is fail-closed by construction: one subprocess call, one provider, no
retries and no alternate tier. ``--ignore-user-config`` is load bearing — without
it Hermes reads the user config and may switch to its configured fallback
providers on auth, quota or transport errors.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "PROVIDER",
    "CodexClient",
    "CodexError",
    "CodexUnavailableError",
    "complete",
]

PROVIDER: Final = "openai-codex"
DEFAULT_MODEL: Final = "gpt-5.6-sol"
DEFAULT_TIMEOUT: Final = 180.0

BINARY_ENV: Final = "AUTOPHAGY_HERMES_BIN"
MODEL_ENV: Final = "AUTOPHAGY_CODEX_MODEL"

_IGNORE_USER_CONFIG: Final = "--ignore-user-config"
_TASK_MODE: Final = "todo"
_CHILD_PATH: Final = "/usr/bin:/bin"
_RELATIVE_BINARY: Final = (".local", "bin", "hermes")
_STDERR_TAIL_LIMIT: Final = 200
_SECRET: Final = re.compile(
    r"(?:sk-[A-Za-z0-9_-]+|Bearer\s+\S+|eyJ[A-Za-z0-9_.-]{16,}|[A-Za-z0-9_-]{32,})"
)


class CodexError(RuntimeError):
    """This Codex request failed."""


class CodexUnavailableError(CodexError):
    """The Codex OAuth tier itself is unavailable (auth, quota, transport)."""


@dataclass(frozen=True, slots=True)
class CodexClient:
    """Non-interactive Codex OAuth caller bound to one binary, home and model."""

    binary: str
    home: str
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> CodexClient:
        source: Mapping[str, str] = os.environ if env is None else env
        home = (source.get("HOME") or "").strip()
        if not home:
            raise CodexUnavailableError("HOME is unset; Codex OAuth credentials cannot be located")
        model = (source.get(MODEL_ENV) or "").strip() or DEFAULT_MODEL
        return cls(binary=_resolve_binary(source, home), home=home, model=model, timeout=timeout)

    def with_model(self, model: str) -> CodexClient:
        return replace(self, model=model)

    def argv(self, prompt: str) -> list[str]:
        return [
            self.binary,
            _IGNORE_USER_CONFIG,
            "-z",
            prompt,
            "--provider",
            PROVIDER,
            "-m",
            self.model,
            "-t",
            _TASK_MODE,
        ]

    def complete(self, prompt: str, *, timeout: float | None = None) -> str:
        """Run one Codex OAuth completion and return its stripped stdout.

        Raises :class:`CodexUnavailableError` when the tier cannot answer and
        :class:`CodexError` when it answers with nothing. Never falls back.
        """
        limit = self.timeout if timeout is None else timeout
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                self.argv(prompt),
                cwd=tempfile.gettempdir(),
                env={"HOME": self.home, "PATH": _CHILD_PATH},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            raise CodexUnavailableError(f"Codex call timed out after {limit:g}s") from None
        except OSError as error:
            raise CodexUnavailableError(
                f"Codex binary could not be executed: {error.__class__.__name__}"
            ) from None
        if completed.returncode != 0:
            tail = _redacted_tail(completed.stderr)
            raise CodexUnavailableError(f"Codex call failed (rc={completed.returncode}): {tail}")
        answer = (completed.stdout or "").strip()
        if not answer:
            raise CodexError("Codex returned an empty completion")
        return answer


def complete(
    prompt: str,
    *,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> str:
    """Convenience one-shot completion for callers that hold no client."""
    client = CodexClient.from_environment(env, timeout=timeout)
    if model:
        client = client.with_model(model)
    return client.complete(prompt)


def _resolve_binary(env: Mapping[str, str], home: str) -> str:
    override = (env.get(BINARY_ENV) or "").strip()
    if override:
        return override
    candidate = Path(home, *_RELATIVE_BINARY)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    found = shutil.which("hermes", path=env.get("PATH"))
    if found:
        return found
    raise CodexUnavailableError("hermes binary not found; Codex OAuth is unavailable")


def _redacted_tail(stderr: str | None) -> str:
    collapsed = " ".join((stderr or "").split())
    if not collapsed:
        return "<no stderr>"
    return _SECRET.sub("<redacted>", collapsed)[-_STDERR_TAIL_LIMIT:]
