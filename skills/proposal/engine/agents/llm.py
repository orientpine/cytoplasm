from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar, cast

from ..contracts.protocols import LLMClient


class CacheMissError(ValueError):
    """Raised when a cache entry is missing in replay mode."""


class CachingLLMClient:
    """LLMClient wrapper that caches completions for deterministic replay.

    Modes:
      replay (default): cache hit→return, miss→CacheMissError (no network)
      record: cache hit→return, miss→call backend→store→return
    """

    def __init__(
        self,
        backend: LLMClient,
        cache: dict[tuple[str, str], str],
        mode: str = "replay",
        cache_path: str | Path | None = None,
    ) -> None:
        if mode not in {"replay", "record"}:
            raise ValueError(f"Unsupported cache mode: {mode!r}")
        self._backend: LLMClient = backend
        self._cache: dict[tuple[str, str], str] = cache
        self._mode: str = mode
        self._cache_path: str | Path | None = cache_path
        # The parallel reviewer pass calls ``complete`` from worker threads; each save
        # must see a dict no other thread is resizing.
        self._lock: threading.Lock = threading.Lock()

    def complete(self, role: str, prompt: str) -> str:
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        key = (role, prompt_hash)
        if key in self._cache:
            return self._cache[key]
        if self._mode == "replay":
            message = "".join(
                [
                    f"Cache miss in replay mode: role={role!r}, ",
                    f"prompt_hash={prompt_hash!r}. ",
                    f"Fixture key: {role}:{prompt_hash}. ",
                    "Re-run with --mode record to populate the cache.",
                ]
            )
            raise CacheMissError(message)
        response = self._backend.complete(role, prompt)
        if not response.strip():
            message = "".join(
                [
                    f"Empty/blank response from backend: role={role!r}, ",
                    f"prompt_hash={prompt_hash!r}. ",
                    "Do not commit empty cache entries.",
                ]
            )
            raise ValueError(message)
        with self._lock:
            self._cache[key] = response
            if self._cache_path is not None:
                save_cache(self._cache_path, self._cache)
        return response


class MockLLMClient:
    _record_buffer: ClassVar[list[dict[str, str]] | None] = None

    def __init__(self, responses: dict[tuple[str, str], str]) -> None:
        self.responses: dict[tuple[str, str], str] = dict(responses)

    def complete(self, role: str, prompt: str) -> str:
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        key = (role, prompt_hash)
        try:
            return self.responses[key]
        except KeyError as exc:
            if self._record_buffer is not None:
                self._record_buffer.append(
                    {
                        "role": role,
                        "prompt": prompt,
                        "prompt_hash": prompt_hash,
                        "fixture_key": f"{role}:{prompt_hash}",
                    }
                )
                return ""
            available = ", ".join(f"{known_role}:{known_hash}" for known_role, known_hash in self.responses)
            message = (
                "No mock LLM response for "
                f"role={role!r}, prompt_hash={prompt_hash!r}. "
                f"Fixture key: {role}:{prompt_hash}. "
                f"Available keys: {available or '<none>'}"
            )
            raise KeyError(message) from exc

    @classmethod
    @contextmanager
    def record_mode(cls, record_buffer: list[dict[str, str]] | None = None) -> Iterator[list[dict[str, str]]]:
        previous = cls._record_buffer
        buffer = record_buffer if record_buffer is not None else []
        cls._record_buffer = buffer
        try:
            yield buffer
        finally:
            cls._record_buffer = previous


def load_mock_responses(path: str | Path) -> dict[tuple[str, str], str]:
    data = cast("dict[str, str]", json.loads(Path(path).read_text(encoding="utf-8")))
    responses: dict[tuple[str, str], str] = {}
    for fixture_key, response in data.items():
        role, separator, prompt_hash = fixture_key.partition(":")
        if not role or separator != ":" or not prompt_hash:
            raise ValueError(f"Invalid mock LLM fixture key: {fixture_key!r}")
        responses[(role, prompt_hash)] = response
    return responses


def save_cache(path: str | Path, responses: dict[tuple[str, str], str]) -> None:
    """Replace the cache file atomically with an owner-only (0600) copy.

    A process killed mid-save leaves the previous complete file, never a torn one,
    so a later ``record`` run can still resume from it.
    """
    data = {f"{role}:{prompt_hash}": response for (role, prompt_hash), response in responses.items()}
    target = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def get_llm(config: dict[str, object]) -> LLMClient:
    """Factory: returns the right LLMClient based on config.

    Config keys:
      mock (bool): True → MockLLMClient with responses/responses_path
      mode (str): "live"|"replay"|"record" → CachingLLMClient wrapping backend
      cache_path (str): path to cache JSON file (for CachingLLMClient)
      provider (str): "hermes" (Codex OAuth by way of the hermes subprocess)
      responses (dict): mock responses (for mock mode)
      responses_path (str): path to responses JSON (for mock mode)

    Default (config={}): returns MockLLMClient with empty responses.
    Pipeline default (llm=None): MockLLMClient(load_mock_responses(DEFAULT_LLM_FIXTURE)).
    """
    if config.get("mock") or not any(key in config for key in ("mode", "cache_path", "provider")):
        responses = config.get("responses")
        if responses is None and config.get("responses_path"):
            responses = load_mock_responses(str(config["responses_path"]))
        return MockLLMClient(cast("dict[tuple[str, str], str]", responses or {}))

    provider = str(config.get("provider", "hermes"))
    if provider != "hermes":
        raise ValueError(f"Unsupported LLM provider: {provider!r}")
    from .hermes_client import hermes_client_from_env

    from .writer_guidance import SectionGuidedLLM

    backend: LLMClient = SectionGuidedLLM(hermes_client_from_env())

    mode = str(config.get("mode", "replay"))
    if mode == "live":
        return CachingLLMClient(backend=backend, cache={}, mode="record", cache_path=None)

    if mode not in {"replay", "record"}:
        raise ValueError(f"Unsupported cache mode: {mode!r}")

    cache_path = config.get("cache_path")
    cache: dict[tuple[str, str], str] = {}
    if cache_path:
        try:
            cache = load_mock_responses(str(cache_path))
        except FileNotFoundError:
            if mode == "replay":
                raise

    return CachingLLMClient(
        backend=backend,
        cache=cache,
        mode=mode,
        cache_path=str(cache_path) if cache_path else None,
    )


__all__ = [
    "CacheMissError",
    "CachingLLMClient",
    "MockLLMClient",
    "get_llm",
    "load_mock_responses",
    "save_cache",
]
