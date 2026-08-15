"""Fail-closed LLM routing for private document-type extraction and drafting."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Final


GLM_MODEL: Final = "glm-main"
CODEX_PROVIDER: Final = "openai-codex"
CODEX_MODEL: Final = "gpt-5.4"


class PatentRoutingError(RuntimeError):
    """A caller attempted to route sensitivity-gated text to GLM."""


class LlmCallError(RuntimeError):
    """A private one-shot model call did not return a usable response."""


def _log_path() -> Path:
    return Path(os.environ.get("DOCTYPE_LLM_LOG", "~/.hermes/doctype/logs/llm-calls.jsonl")).expanduser()


def _log_call(*, provider: str, model: str, purpose: str, sensitive: bool, opaque_id: str) -> None:
    """Append only masked routing facts; prompt and completion bodies are forbidden."""
    path = _log_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    record = {
        "model": model,
        "opaque_id": opaque_id,
        "provider": provider,
        "purpose": purpose,
        "sensitive": sensitive,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _ = handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)


def _run_stub(binary: str, prompt: str, timeout: float) -> str:
    try:
        completed = subprocess.run(
            [binary], input=prompt, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LlmCallError(error.__class__.__name__) from error
    if completed.returncode != 0 or not completed.stdout.strip():
        raise LlmCallError(f"GLM stub rc={completed.returncode}")
    return completed.stdout.strip()


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
    raise LlmCallError("LITELLM_AGENT_KEY is required for a GLM call")


def call_glm(
    prompt: str,
    *,
    sensitive: bool,
    purpose: str = "unspecified",
    opaque_id: str = "-",
    timeout: float = 180.0,
) -> str:
    """Call GLM only for explicitly non-sensitive paths; sensitive input fails closed."""
    if sensitive:
        raise PatentRoutingError("sensitivity gate hit: GLM is forbidden for this document")
    stub = os.environ.get("DOCTYPE_GLM_BIN", "")
    if stub:
        result = _run_stub(stub, prompt, timeout)
        _log_call(provider=GLM_MODEL, model=GLM_MODEL, purpose=purpose, sensitive=False, opaque_id=opaque_id)
        return result
    base_url = os.environ.get("DOCTYPE_LITELLM_BASE_URL", "http://127.0.0.1:4000/v1")
    payload = json.dumps(
        {
            "model": GLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "metadata": {"tags": ["doctype"]},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {_litellm_key()}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            decoded = json.loads(response.read().decode("utf-8"))
        result = decoded["choices"][0]["message"]["content"]
    except (KeyError, OSError, TypeError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise LlmCallError(f"GLM call failed: {error.__class__.__name__}") from None
    if not isinstance(result, str) or not result.strip():
        raise LlmCallError("GLM returned no text")
    _log_call(provider=GLM_MODEL, model=GLM_MODEL, purpose=purpose, sensitive=False, opaque_id=opaque_id)
    return result.strip()


def call_codex(
    prompt: str,
    *,
    purpose: str = "unspecified",
    sensitive: bool = False,
    opaque_id: str = "-",
    timeout: float = 600.0,
) -> str:
    """Use the mandatory non-GLM Korean/gist tier and log only masked routing facts."""
    binary = os.environ.get("DOCTYPE_HERMES_BIN") or shutil.which("hermes") or "~/.local/bin/hermes"
    command = [binary, "-z", prompt, "--provider", CODEX_PROVIDER, "-m", CODEX_MODEL, "-t", "todo"]
    environment = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
    try:
        completed = subprocess.run(
            command,
            cwd=Path.home(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LlmCallError(error.__class__.__name__) from error
    if completed.returncode != 0 or not completed.stdout.strip():
        raise LlmCallError(f"Codex one-shot rc={completed.returncode}")
    _log_call(provider=CODEX_PROVIDER, model=CODEX_MODEL, purpose=purpose, sensitive=sensitive, opaque_id=opaque_id)
    return completed.stdout.strip()
