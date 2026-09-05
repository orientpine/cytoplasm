"""LLM extraction routing + strict JSON parsing for meeting ingest.

Routing contract (constraint 6):
- Every extraction — patent-sensitive or not — runs on the Codex OAuth tier
  through the shared `automation.codex_llm` client (provider ``openai-codex``).
  There is no second tier, so there is nothing to downgrade to: an unavailable
  tier fails the ingest visibly instead of answering from somewhere else.
- The sensitivity gate is unchanged and still decides confinement and
  sanitization. `call_codex` additionally REFUSES any route that is not the
  Codex OAuth tier — the same fail-closed layer, pointed at the surviving tier.
- The repository client is imported lazily (skills must not import automation
  eagerly); an ImportError refuses the call rather than calling a model itself.
"""

from __future__ import annotations

import os
import sys
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
    "CODEX_PROVIDER",
    "Decision",
    "Extraction",
    "ExtractionParseError",
    "ExtractionUnavailableError",
    "MeetingHeader",
    "NextMeeting",
    "OpenQuestion",
    "ResolvedAction",
    "SpeakerRef",
    "PatentRoutingError",
    "Topic",
    "build_prompt",
    "call_codex",
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
CODEX_PROVIDER: Final = "openai-codex"
_PROMPT_MARKER: Final = "<<<PROMPT>>>"
_REPO_ROOT_ENV: Final = "AUTOPHAGY_REPO_ROOT"
_RELEASE_ROOT: Final = Path("/srv/autophagy-agent-current")


class PatentRoutingError(Exception):
    """Raised when extraction is asked to run anywhere but the Codex OAuth tier."""


class ExtractionUnavailableError(ExtractionParseError):
    """The Codex OAuth tier could not answer this extraction.

    Subclasses `ExtractionParseError` so every existing caller keeps failing
    closed on the same path (meeting_cli exit 6, refusal logged) while the
    recorded error name still distinguishes an unavailable tier from bad JSON.
    Nothing retries and nothing falls back — there is no other tier.
    """


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


def _repo_root() -> Path:
    """Resolve the repository without importing it — mounted skills run outside it."""
    override = os.environ.get(_REPO_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    for parent in Path(__file__).resolve().parents:
        if (parent / "automation" / "skill_mount.py").is_file():
            return parent
    return _RELEASE_ROOT


def call_codex(
    prompt: str,
    *,
    sensitive: bool = False,
    provider: str = CODEX_PROVIDER,
    timeout: float | None = None,
) -> str:
    """Run one extraction on the Codex OAuth tier — the only route there is.

    ``sensitive`` no longer picks a provider; it is kept so the refusal below
    can say what was at stake. ``provider`` exists for the same reason: a caller
    that asks for any other tier is refused BEFORE the prompt leaves this
    process — the same guard that used to keep patent text off the second tier.

    The shared client owns the transport (``-t todo`` inert toolset,
    ``--ignore-user-config`` so the user config's fallback providers cannot
    fire). An unavailable tier raises instead of answering from elsewhere.
    """
    if provider != CODEX_PROVIDER:
        detail = " (patent-sensitive)" if sensitive else ""
        raise PatentRoutingError(
            f"extraction must run on {CODEX_PROVIDER}; refused route {provider!r}{detail}"
        )
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from automation.codex_llm import CodexClient, CodexError  # noqa: PLC0415
    except ImportError as error:
        raise ExtractionUnavailableError(
            f"shared Codex client unavailable ({type(error).__name__}); extraction refused"
        ) from error
    budget = _budget() if timeout is None else timeout
    try:
        return CodexClient.from_environment(timeout=budget).complete(prompt)
    except CodexError as error:
        raise ExtractionUnavailableError(f"codex extraction failed: {error}") from error


def extract(
    meeting_text: str,
    *,
    sensitive: bool,
    prompt_path: Path,
    my_names: str,
    recorded_response: str | None = None,
    evidence: str = "",
    slides: str = "",
    open_actions: str = "",
) -> tuple[Extraction, str]:
    """Build the prompt, run it on the Codex OAuth tier, return (extraction, provider).

    ``sensitive`` no longer selects a tier — it travels with the call so the
    route guard can name it in a refusal, and the caller keeps using it for
    confinement and sanitization exactly as before.
    """
    prompt = build_prompt(
        load_prompt_template(prompt_path), meeting_text=meeting_text, my_names=my_names,
        evidence=evidence, slides=slides, open_actions=open_actions,
    )
    if recorded_response is not None:
        return parse_extraction(recorded_response), "recorded"
    return parse_extraction(call_codex(prompt, sensitive=sensitive)), CODEX_PROVIDER
