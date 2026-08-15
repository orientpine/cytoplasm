"""LLM extraction routing + strict JSON parsing for meeting ingest.

Routing contract (constraint 6):
- non-sensitive  -> LiteLLM `glm-main` (loopback gateway, agent virtual key)
- patent-sensitive -> `hermes -z ... --provider openai-codex -m gpt-5.4`
  (ChatGPT OAuth path; NEVER LiteLLM/GLM). `call_litellm` refuses sensitive
  input outright, and would tag it `patent-sensitive` anyway so the W1-1
  gateway guard 403s it — two independent fail-closed layers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

GLM_MODEL: Final = "glm-main"
CODEX_MODEL: Final = "gpt-5.4"
_PROMPT_MARKER: Final = "<<<PROMPT>>>"


class PatentRoutingError(Exception):
    """Raised when patent-sensitive text is about to reach a GLM tier."""


class ExtractionParseError(Exception):
    """Raised when the LLM response does not contain the required JSON."""


@dataclass(frozen=True, slots=True)
class ActionItem:
    """One extracted todo/milestone/other-owner item."""

    title: str
    deadline: str | None
    basis: str
    owner: str | None = None


@dataclass(frozen=True, slots=True)
class Extraction:
    """Validated extraction payload."""

    decisions: tuple[str, ...] = ()
    todos: tuple[ActionItem, ...] = ()
    milestones: tuple[ActionItem, ...] = ()
    others: tuple[ActionItem, ...] = ()


def load_prompt_template(path: Path) -> str:
    """Return the prompt body below the marker LINE.

    Anchored to a line that IS the marker (not a substring): the doc header
    legitimately MENTIONS `<<<PROMPT>>>` in prose, and a substring split once
    leaked header text (including "변경 시 버전 파일명을 올린다") into the LLM
    prompt — codex then literally created v3/v4 template files.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == _PROMPT_MARKER:
            return "\n".join(lines[index + 1 :]).strip()
    raise ValueError(f"prompt file missing {_PROMPT_MARKER} line: {path}")


def build_prompt(template: str, *, meeting_text: str, my_names: str) -> str:
    """Substitute the two placeholders; refuse if either is missing."""
    if "{{MEETING_TEXT}}" not in template or "{{MY_NAMES}}" not in template:
        raise ValueError("prompt template missing required placeholders")
    return template.replace("{{MY_NAMES}}", my_names).replace(
        "{{MEETING_TEXT}}", meeting_text
    )


def _clean_deadline(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def _clean_items(raw: object, *, with_owner: bool) -> tuple[ActionItem, ...]:
    if not isinstance(raw, list):
        return ()
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        items.append(
            ActionItem(
                title=title,
                deadline=_clean_deadline(entry.get("deadline")),
                basis=str(entry.get("basis") or "").strip(),
                owner=str(entry.get("owner") or "").strip() or None
                if with_owner
                else None,
            )
        )
    return tuple(items)


def parse_extraction(raw: str) -> Extraction:
    """Extract the first balanced JSON object from raw text and validate it."""
    start = raw.find("{")
    if start < 0:
        raise ExtractionParseError("no JSON object in LLM response")
    depth = 0
    end = -1
    in_string = False
    escape = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        raise ExtractionParseError("unbalanced JSON object in LLM response")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as error:
        raise ExtractionParseError(f"invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ExtractionParseError("LLM response JSON is not an object")
    decisions = tuple(
        str(item).strip()
        for item in (payload.get("decisions") or [])
        if str(item).strip()
    )
    return Extraction(
        decisions=decisions,
        todos=_clean_items(payload.get("todos"), with_owner=False),
        milestones=_clean_items(payload.get("milestones"), with_owner=False),
        others=_clean_items(payload.get("others"), with_owner=True),
    )


def call_litellm(
    prompt: str,
    *,
    sensitive: bool,
    base_url: str,
    api_key: str,
    timeout: float = 180.0,
) -> str:
    """Call LiteLLM glm-main for NON-sensitive extraction only (fail closed)."""
    if sensitive:
        raise PatentRoutingError(
            "patent-sensitive text must never be sent to a GLM tier"
        )
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(
            {
                "model": GLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "metadata": {"tags": ["meeting-ingest"]},
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def call_codex(prompt: str, *, timeout: float = 600.0) -> str:
    """Run the non-GLM extraction through the hermes openai-codex one-shot.

    ``-t todo`` is load-bearing: without an explicit harmless toolset the
    one-shot agent gets file/terminal tools and has been observed EDITING
    local files instead of answering (it rewrote the mounted skill's prompt
    templates). ``todo`` grants only the inert todo-list tool.
    """
    completed = subprocess.run(
        [
            "hermes",
            "-z",
            prompt,
            "--provider",
            "openai-codex",
            "-m",
            CODEX_MODEL,
            "-t",
            "todo",
        ],
        capture_output=True,
        timeout=timeout,
        cwd=os.path.expanduser("~"),
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")[-400:]
        raise ExtractionParseError(
            f"codex one-shot failed rc={completed.returncode}: {stderr}"
        )
    return stdout


def extract(
    meeting_text: str,
    *,
    sensitive: bool,
    prompt_path: Path,
    my_names: str,
    base_url: str,
    api_key: str,
    recorded_response: str | None = None,
) -> tuple[Extraction, str]:
    """Route by sensitivity, parse, and return (extraction, provider_used)."""
    prompt = build_prompt(
        load_prompt_template(prompt_path), meeting_text=meeting_text, my_names=my_names
    )
    if recorded_response is not None:
        return parse_extraction(recorded_response), "recorded"
    if sensitive:
        return parse_extraction(call_codex(prompt)), "openai-codex"
    raw = call_litellm(
        prompt, sensitive=False, base_url=base_url, api_key=api_key
    )
    return parse_extraction(raw), GLM_MODEL
