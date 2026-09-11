"""화자 수 질의의 실행 경계 — 옵트인이고, 민감도 게이트를 통과한 초안만 모델에 준다.

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
RULES_RELPATH: Final = ("configs", "sensitivity-rules.yaml")
#: 초안이 길어도 화자 수를 세는 데 필요한 것은 대화의 짜임이다.
MAX_DRAFT_CHARS: Final = 20_000
_ANCHOR: Final = "<<<PROMPT>>>"
_PLACEHOLDER: Final = "{{TRANSCRIPT}}"
_PATENT_TAG: Final = "patent-sensitive"


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
    gate = _gate(repo_root.joinpath(*RULES_RELPATH))
    if gate is None:
        # 게이트를 못 읽으면 묻지 않는다 — 특허 녹취가 검증 없이 나가는 것보다 낫다.
        print("RECOUNT-FAIL rules-unreadable", file=sys.stderr)
        return None
    ask = complete if complete is not None else _codex(env)
    if ask is None:
        print("RECOUNT-FAIL no-llm", file=sys.stderr)
        return None

    def _ask(draft: str) -> str:
        if _PATENT_TAG in gate(draft) and not _verified(ask):
            # 규칙이 허용하는 경로는 Codex OAuth 하나뿐이고, 그 경로면 묻는 것이 규칙을
            # 지키는 것이다. 출처를 확인할 수 없는 completer 만 거른다(fail-closed).
            print("RECOUNT-SKIP patent-sensitive", file=sys.stderr)
            return ""
        return ask(template.replace(_PLACEHOLDER, draft[:MAX_DRAFT_CHARS]))

    return _ask


def _verified(ask: Callable[[str], str]) -> bool:
    """민감도 규칙이 이름으로 허용한 Codex OAuth 경로인가 — 확인 못 하면 아니다."""
    try:
        from automation.codex_llm import route_is_verified  # noqa: PLC0415 - 런타임 의존
    except Exception:  # noqa: BLE001 - 확인할 수 없으면 허용하지 않는다(fail-closed)
        return False
    return route_is_verified(ask)


def _template(path: Path) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    _, anchor, body = raw.partition(_ANCHOR)
    return body.strip() if anchor and _PLACEHOLDER in body else None


def _gate(path: Path) -> Callable[[str], frozenset[str]] | None:
    try:
        from automation.rag_ingest.sensitivity import (  # noqa: PLC0415 - 런타임 의존
            classify,
            load_rules,
        )

        rules = load_rules(path)
    except Exception:  # noqa: BLE001 - 못 읽으면 묻지 않는다(fail-closed)
        return None
    return lambda text: classify(text, rules)


def _codex(env: Mapping[str, str]) -> Callable[[str], str] | None:
    """저장소의 유일한 모델 호출 경로. 쓸 수 없으면 다른 provider 로 내려가지 않는다."""
    try:
        from automation.codex_llm import (  # noqa: PLC0415 - 런타임 의존
            CodexClient,
            CodexError,
            VerifiedRoute,
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

    # 민감도 규칙이 이름으로 허용한 그 경로임을 표시해 내보낸다.
    return VerifiedRoute(_complete)
