"""화자 수 질의의 실행 경계 — 옵트인이고, 초안을 계정 모델에 준다.

판정은 `stt_speaker_count` 가 하고 여기에는 "물어도 되는가"와 "무엇을 보내는가"만 있다.
설정하지 않은 노드에서는 `resolve` 가 None 을 돌려주고, 그때 전사는 1차 화자 분리 결과를
그대로 써서 예전과 바이트 그대로다.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

ENABLE_ENV: Final = "SPEECHTOTEXT_SPEAKER_COUNT_LLM"
PROMPT_RELPATH: Final = ("prompts", "speaker-count-v1.md")
#: 초안이 길어도 화자 수를 세는 데 필요한 것은 대화의 짜임이다.
MAX_DRAFT_CHARS: Final = 20_000
_ANCHOR: Final = "<<<PROMPT>>>"
_PLACEHOLDER: Final = "{{TRANSCRIPT}}"


def resolve(
    env: Mapping[str, str],
    *,
    repo_root: Path,
    complete: Callable[[str], str] | None = None,
) -> Callable[[str], str] | None:
    """묻는 함수, 또는 물을 수 없을 때 None(그 경우 1차 분리 결과를 그대로 쓴다)."""
    if env.get(ENABLE_ENV) != "1":
        return None
    root = str(repo_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    template = _template(repo_root.joinpath(*PROMPT_RELPATH))
    if template is None:
        print("RECOUNT-FAIL prompt-unreadable", file=sys.stderr)
        return None
    ask = complete if complete is not None else _codex(env)
    if ask is None:
        print("RECOUNT-FAIL no-llm", file=sys.stderr)
        return None

    def _ask(draft: str) -> str:
        return ask(template.replace(_PLACEHOLDER, draft[:MAX_DRAFT_CHARS]))

    return _ask


def _template(path: Path) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    _, anchor, body = raw.partition(_ANCHOR)
    return body.strip() if anchor and _PLACEHOLDER in body else None


def _codex(env: Mapping[str, str]) -> Callable[[str], str] | None:
    """저장소의 유일한 모델 호출 경로. 계정 설정의 주 모델과 폴백을 따른다."""
    try:
        from automation.codex_llm import (  # noqa: PLC0415 - 런타임 의존
            CodexClient,
            CodexError,
        )

        client = CodexClient.from_environment(env)
    except Exception:  # noqa: BLE001 - 자격증명·바이너리 부재는 질의 없음일 뿐이다
        return None

    def _complete(prompt: str) -> str:
        try:
            return client.complete(prompt)
        except CodexError as failure:
            print(f"RECOUNT-FAIL {type(failure).__name__}", file=sys.stderr)
            return ""

    return _complete
