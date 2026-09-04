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
import subprocess
import urllib.request
from pathlib import Path
from typing import Final

from meeting_schema import (
    ActionItem,
    Decision,
    Extraction,
    ExtractionParseError,
    MeetingHeader,
    NextMeeting,
    OpenQuestion,
    ResolvedAction,
    SpeakerRef,
    Topic,
    map_extraction,
    parse_extraction,
)

__all__ = [
    "ActionItem",
    "CODEX_MODEL",
    "Decision",
    "Extraction",
    "ExtractionParseError",
    "GLM_MODEL",
    "MeetingHeader",
    "NextMeeting",
    "OpenQuestion",
    "ResolvedAction",
    "SpeakerRef",
    "PatentRoutingError",
    "Topic",
    "build_prompt",
    "call_codex",
    "call_litellm",
    "extract",
    "load_prompt_template",
    "map_extraction",
    "parse_extraction",
]

#: 노드 실측(2026-08-28): 38,499자 전사본(프롬프트 40,130자)의 왕복이 **258.9초**였다.
#: 옛 기본값 180초는 그 아래라 `TimeoutError` 로 죽었고, 야간 배치는 매일 밤 같은 자리에서
#: 실패했다 — 재시도가 있어도 한도가 그대로면 영원히 실패한다. 600초는 그 실측의 2.3배다.
LLM_TIMEOUT: Final = 600.0
TIMEOUT_ENV: Final = "MEETING_LLM_TIMEOUT"
GLM_MODEL: Final = "glm-main"
CODEX_MODEL: Final = "gpt-5.4"
_PROMPT_MARKER: Final = "<<<PROMPT>>>"


class PatentRoutingError(Exception):
    """Raised when patent-sensitive text is about to reach a GLM tier."""


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


def build_prompt(
    template: str, *, meeting_text: str, my_names: str, evidence: str = "", slides: str = "",
    open_actions: str = ""
) -> str:
    """Substitute source material and append the optional bounded evidence block.

    ``{{SLIDES}}`` sits INSIDE the template, before the closing instruction: the v1/v2
    post-mortem in the prompt file says material after the instruction gets echoed back.
    """
    if "{{MEETING_TEXT}}" not in template or "{{MY_NAMES}}" not in template:
        raise ValueError("prompt template missing required placeholders")
    prompt = (
        template.replace("{{MY_NAMES}}", my_names)
        .replace("{{MEETING_TEXT}}", meeting_text)
        .replace("{{SLIDES}}", slides)
        .replace("{{OPEN_ACTIONS}}", open_actions)
    )
    if not evidence:
        return prompt
    return (
        f"{prompt}\n\n{evidence}\n\n"
        "Use only MATERIAL/EVIDENCE, cite [En], do not invent."
    )


def _budget() -> float:
    """Node-adjustable so a longer meeting does not have to wait for a release."""
    try:
        return float(os.environ[TIMEOUT_ENV])
    except (KeyError, ValueError):
        return LLM_TIMEOUT


def call_litellm(
    prompt: str,
    *,
    sensitive: bool,
    base_url: str,
    api_key: str,
    timeout: float | None = None,
) -> str:
    """Call LiteLLM glm-main for NON-sensitive extraction only (fail closed)."""
    budget = timeout if timeout is not None else _budget()
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
    with urllib.request.urlopen(request, timeout=budget) as response:  # noqa: S310
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
    evidence: str = "",
    slides: str = "",
    open_actions: str = "",
) -> tuple[Extraction, str]:
    """Route by sensitivity, parse, and return (extraction, provider_used)."""
    prompt = build_prompt(
        load_prompt_template(prompt_path), meeting_text=meeting_text, my_names=my_names,
        evidence=evidence, slides=slides, open_actions=open_actions,
    )
    if recorded_response is not None:
        return parse_extraction(recorded_response), "recorded"
    if sensitive:
        return parse_extraction(call_codex(prompt)), "openai-codex"
    raw = call_litellm(
        prompt, sensitive=False, base_url=base_url, api_key=api_key
    )
    return parse_extraction(raw), GLM_MODEL
