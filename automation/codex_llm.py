"""Single shared Hermes LLM client — the only model call path in this repository.

Every automation, skill, cron and batch caller goes through :class:`CodexClient`.
The primary route is pinned in argv: provider ``openai-codex``, model ``gpt-5.6-sol``.
When that tier cannot answer (quota, rate limit, auth, transport), Hermes itself
switches to the ``fallback_providers`` chain in the account's ``~/.hermes/config.yaml``
— the same chain the Discord gateway uses (owner decision 2026-09-22: xAI Grok).
That is why the call does NOT pass ``--ignore-user-config``: that flag drops the
user config and, with it, the fallback chain. This client makes one subprocess call
and never retries on its own; when the whole Hermes chain fails, the error propagates.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
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
    "Served",
    "UNKNOWN",
    "VerifiedRoute",
    "complete",
    "complete_served",
    "route_is_verified",
    "served_route",
]

PROVIDER: Final = "openai-codex"
DEFAULT_MODEL: Final = "gpt-5.6-sol"
DEFAULT_TIMEOUT: Final = 180.0

BINARY_ENV: Final = "AUTOPHAGY_HERMES_BIN"
MODEL_ENV: Final = "AUTOPHAGY_CODEX_MODEL"

#: Recorded when Hermes wrote no readable usage report — never a guess.
UNKNOWN: Final = "unknown"

_USAGE_FLAG: Final = "--usage-file"
_TASK_MODE: Final = "todo"
_CHILD_PATH: Final = "/usr/bin:/bin"
_RELATIVE_BINARY: Final = (".local", "bin", "hermes")
_STDERR_TAIL_LIMIT: Final = 200
_SECRET: Final = re.compile(
    r"(?:sk-[A-Za-z0-9_-]+|Bearer\s+\S+|eyJ[A-Za-z0-9_.-]{16,}|[A-Za-z0-9_-]{32,})"
)


@dataclass(frozen=True, slots=True)
class VerifiedRoute:
    """A completer that IS the route the sensitivity rules already permit.

    ``configs/sensitivity-rules.yaml`` says the tag ``patent-sensitive`` permits only
    the shared Hermes route — Codex OAuth (openai-codex) as primary plus the account's
    configured ``fallback_providers`` chain. A gate therefore has two separate questions
    to answer — "is this text sensitive?" and "is this route permitted?" — and only the
    second one decides whether the call may happen. Wrapping the Codex completer states
    that answer in the type system, so a gate can let permitted text through the one
    route the rules allow while a completer of unknown provenance stays refused,
    because an arbitrary callable is not this wrapper.
    """

    complete: Callable[[str], str]
    provider: str = PROVIDER

    def __call__(self, prompt: str) -> str:
        return self.complete(prompt)


def route_is_verified(completer: object) -> bool:
    """True only for the Codex OAuth route the sensitivity rules permit."""
    return isinstance(completer, VerifiedRoute) and completer.provider == PROVIDER


@dataclass(frozen=True, slots=True)
class Served:
    """One answer plus the route that actually produced it.

    The argv pins the primary, but Hermes may answer from the account's
    ``fallback_providers`` chain. Routing logs that record only the requested
    primary therefore cannot say where a patent-sensitive prompt really went;
    ``provider``/``model`` here come from Hermes' own ``--usage-file`` report.
    """

    text: str
    provider: str
    model: str


def served_route(usage_path: Path) -> tuple[str, str]:
    """``(provider, model)`` from a Hermes ``--usage-file`` report; ``unknown`` when absent."""
    try:
        report = json.loads(usage_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNKNOWN, UNKNOWN
    if not isinstance(report, dict):
        return UNKNOWN, UNKNOWN
    provider = str(report.get("provider") or "").strip() or UNKNOWN
    model = str(report.get("model") or "").strip() or UNKNOWN
    return provider, model


class CodexError(RuntimeError):
    """This Codex request failed."""


class CodexUnavailableError(CodexError):
    """No route could answer: Codex OAuth and the configured fallback chain all failed."""


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
        """Run one completion (Codex OAuth first) and return its stripped stdout.

        Hermes may answer from the configured ``fallback_providers`` chain when Codex
        cannot; this client itself never retries. Raises :class:`CodexUnavailableError`
        when no route answers and :class:`CodexError` when it answers with nothing.
        """
        return self._run(self.argv(prompt), timeout)

    def complete_served(self, prompt: str, *, timeout: float | None = None) -> Served:
        """Like :meth:`complete`, plus the provider and model that actually answered.

        The report lives in a private temp file that is removed after the call; a
        missing or unreadable report yields ``unknown`` instead of assuming Codex.
        """
        handle, name = tempfile.mkstemp(prefix="autophagy-usage-", suffix=".json")
        os.close(handle)
        usage = Path(name)
        try:
            text = self._run([*self.argv(prompt), _USAGE_FLAG, str(usage)], timeout)
            provider, model = served_route(usage)
        finally:
            usage.unlink(missing_ok=True)
        return Served(text=text, provider=provider, model=model)

    def _run(self, argv: list[str], timeout: float | None) -> str:
        limit = self.timeout if timeout is None else timeout
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv,
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


def complete_served(
    prompt: str,
    *,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> Served:
    """One-shot :meth:`CodexClient.complete_served` for callers that hold no client."""
    client = CodexClient.from_environment(env, timeout=timeout)
    if model:
        client = client.with_model(model)
    return client.complete_served(prompt)


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
