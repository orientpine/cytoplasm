"""Thin, fail-closed LiteLLM gateway client for on-demand distillation."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol, TypeAlias

GLM_MODEL: Final = "glm-main"
_DEFAULT_ENDPOINT: Final = "http://127.0.0.1:4000/v1"
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class LlmConfigurationError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class LlmInvocationError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


class LlmClient(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class LiteLlmClient:
    api_key: str
    endpoint: str
    timeout_seconds: float = 180.0

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> LiteLlmClient:
        api_key = environment.get("LITELLM_AGENT_KEY", "").strip()
        if not api_key:
            raise LlmConfigurationError("LITELLM_AGENT_KEY is required for inferred distillation")
        endpoint = environment.get("TWIN_DISTILL_LITELLM_BASE_URL", _DEFAULT_ENDPOINT).strip()
        if not endpoint:
            raise LlmConfigurationError("TWIN_DISTILL_LITELLM_BASE_URL must not be empty")
        return cls(api_key=api_key, endpoint=endpoint)

    def complete(self, prompt: str) -> str:
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        payload = json.dumps(
            {
                "model": GLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "metadata": {"tags": ["twin-distill"]},
            }
        ).encode("utf-8")
        request = Request(
            f"{self.endpoint.rstrip('/')}/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                decoded = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError) as error:
            raise LlmInvocationError(f"LiteLLM request failed: {error.__class__.__name__}") from None
        return _completion_content(decoded)


def _completion_content(decoded: JsonValue) -> str:
    if not isinstance(decoded, dict):
        raise LlmInvocationError("LiteLLM response is not an object")
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmInvocationError("LiteLLM response has no choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LlmInvocationError("LiteLLM response choice is invalid")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LlmInvocationError("LiteLLM response message is invalid")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LlmInvocationError("LiteLLM response has no candidate text")
    return content
