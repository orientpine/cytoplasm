"""Hermes subprocess LLM backend.

Live calls run only on the node under the agent account; this workstation does
not provide a ``hermes`` binary. Tests therefore inject a fake runner.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from typing import Final


DEFAULT_PROVIDER: Final = "openai-codex"
#: The account's Hermes config picks the model; the argv never names one.
DEFAULT_MODEL: Final = "hermes-config"
_STDERR_HEAD_LENGTH: Final = 500


class HermesClientError(RuntimeError):
    """Hermes did not return a usable completion."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


class HermesLLMClient:
    """Invoke Hermes in one-shot mode for each cache miss."""

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        hermes_bin: str = "hermes",
        runner: Runner = subprocess.run,
        timeout_s: float = 600,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.provider: str = provider
        self.model: str = model
        self._hermes_bin: str = hermes_bin
        self._runner: Runner = runner
        self._timeout_s: float = timeout_s
        self._extra_args: tuple[str, ...] = tuple(extra_args)

    def complete(self, role: str, prompt: str) -> str:
        payload = f"You are acting as {role} in a research proposal pipeline.\n\n{prompt}"
        argv = [
            self._hermes_bin,
            "-z",
            payload,
            "-t",
            "todo",
            *self._extra_args,
        ]
        try:
            completed = self._runner(
                argv,
                check=False,
                text=True,
                capture_output=True,
                timeout=self._timeout_s,
            )
        except FileNotFoundError as exc:
            raise HermesClientError(
                "".join(
                    [
                        f"hermes binary not found: {self._hermes_bin!r}. ",
                        "Live Hermes calls must run on the node under the agent account.",
                    ]
                )
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HermesClientError(
                f"hermes call timed out after {self._timeout_s}s for role={role!r}."
            ) from exc
        except OSError as exc:
            raise HermesClientError(
                f"hermes invocation failed for role={role!r}: {type(exc).__name__}."
            ) from exc

        stdout = completed.stdout or ""
        if completed.returncode != 0 or not stdout.strip():
            stderr = (completed.stderr or "").strip()[:_STDERR_HEAD_LENGTH]
            detail = stderr or "<empty stderr>"
            raise HermesClientError(
                f"hermes completion failed for role={role!r}, rc={completed.returncode}: {detail}"
            )
        return stdout.strip()


def hermes_client_from_env() -> HermesLLMClient:
    """Build the Hermes backend from its dedicated routing environment."""
    return HermesLLMClient(
        DEFAULT_PROVIDER,
        DEFAULT_MODEL,
    )


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "HermesClientError",
    "HermesLLMClient",
    "hermes_client_from_env",
]
