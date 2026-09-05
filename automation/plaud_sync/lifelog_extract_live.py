"""라이프로그 추출의 실배선 — 민감도 게이트 → Codex OAuth 가용성 → 프롬프트 로드 → 단발 호출."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

from automation.codex_llm import CodexClient, CodexUnavailableError
from automation.plaud_sync.lifelog_extract import extract
from automation.plaud_sync.lifelog_model import (
    ExtractionOutcome,
    ExtractionSkipped,
    Extractor,
    LifelogExtractError,
    LifelogRecording,
)
from automation.rag_ingest.sensitivity import (
    SensitivityRules,
    SensitivityRulesError,
    classify,
    load_rules,
)

_PATENT_TAG: Final = "patent-sensitive"
_GATE_REASON: Final = "민감도 게이트"
_NO_RULES_REASON: Final = "민감도 규칙 없음"
_NO_LLM_REASON: Final = "LLM 미설정"
_RULES_RELPATH: Final = ("configs", "sensitivity-rules.yaml")
_TEMPLATE_RELPATH: Final = ("prompts", "lifelog-extraction-v1.md")
_PROMPT_ANCHOR: Final = "<<<PROMPT>>>"
_TIMEOUT_ENV: Final = "PLAUD_SYNC_LLM_TIMEOUT"
_PROMPT_ENV: Final = "PLAUD_SYNC_EXTRACT_PROMPT"
_DEFAULT_TIMEOUT: Final = 120.0


def build_extractor(
    environment: Mapping[str, str],
    *,
    repo_root: Path,
    complete: Callable[[str], str] | None = None,
) -> Extractor:
    """녹취 하나를 추출 결과로 바꾸는 Extractor. 규칙·템플릿은 최초 사용 때 한 번만 읽는다."""
    rules_path = repo_root.joinpath(*_RULES_RELPATH)
    template_path = _template_path(environment, repo_root)
    completer = complete if complete is not None else _live_completer(environment)
    rules_cell: list[SensitivityRules | None] = []
    template_cell: list[str] = []

    def _extract(recording: LifelogRecording) -> ExtractionOutcome:
        if not rules_cell:
            rules_cell.append(_load_rules(rules_path))
        rules = rules_cell[0]
        if rules is None:
            # 게이트를 못 읽으면 모델을 부르지 않는다 (fail-closed).
            return ExtractionSkipped(_NO_RULES_REASON)
        gate_text = f"{recording.summary_markdown}\n{recording.transcript_text}"
        if _PATENT_TAG in classify(gate_text, rules):
            return ExtractionSkipped(_GATE_REASON)
        if completer is None:
            # Codex OAuth 계층을 못 쓰면 추출만 생략한다. 다른 모델로 내려가지 않는다.
            return ExtractionSkipped(_NO_LLM_REASON)
        if not template_cell:
            template_cell.append(_read_template(template_path))
        return extract(recording, template=template_cell[0], complete=completer)

    return _extract


def _template_path(environment: Mapping[str, str], repo_root: Path) -> Path:
    override = environment.get(_PROMPT_ENV, "").strip()
    return Path(override) if override else repo_root.joinpath(*_TEMPLATE_RELPATH)


def _load_rules(path: Path) -> SensitivityRules | None:
    """규칙을 읽지 못하면 None — 호출자가 건너뛰기로 접는 fail-closed 신호."""
    try:
        return load_rules(path)
    except (OSError, ValueError, SensitivityRulesError):
        return None


def _read_template(path: Path) -> str:
    """템플릿 부재는 배포 결함이므로 조용히 넘기지 않고 크게 실패한다 (다음 폴에 재시도)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        raise LifelogExtractError(f"추출 프롬프트를 읽을 수 없다: {path}") from None
    _, anchor, body = raw.partition(_PROMPT_ANCHOR)
    return (body if anchor else raw).lstrip("\n")


def _live_completer(environment: Mapping[str, str]) -> Callable[[str], str] | None:
    """Codex OAuth 단발 호출자. 계층에 닿을 수 없으면 None — 대체 모델은 없다."""
    try:
        client = CodexClient.from_environment(environment, timeout=_timeout(environment))
    except CodexUnavailableError:
        return None
    return client.complete


def _timeout(environment: Mapping[str, str]) -> float:
    try:
        timeout = float(environment.get(_TIMEOUT_ENV, "").strip())
    except ValueError:
        return _DEFAULT_TIMEOUT
    return timeout if timeout > 0 else _DEFAULT_TIMEOUT
