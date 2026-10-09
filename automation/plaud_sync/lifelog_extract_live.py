"""라이프로그 추출의 실배선 — 계정 모델 가용성 → 프롬프트 로드 → 단발 호출."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Final

from automation.codex_llm import (
    CodexClient,
    CodexUnavailableError,
)
from automation.plaud_sync.lifelog_extract import extract, reference_drops, summarize
from automation.plaud_sync.lifelog_model import (
    ExtractionOutcome,
    ExtractionSkipped,
    Extractor,
    LifelogExtractError,
    LifelogRecording,
)

_NO_LLM_REASON: Final = "LLM 미설정"
_TEMPLATE_RELPATH: Final = ("prompts", "lifelog-extraction-v5.md")
_SUMMARY_RELPATH: Final = ("prompts", "lifelog-summary-v1.md")
_PROMPT_ANCHOR: Final = "<<<PROMPT>>>"
_TIMEOUT_ENV: Final = "PLAUD_SYNC_LLM_TIMEOUT"
_PROMPT_ENV: Final = "PLAUD_SYNC_EXTRACT_PROMPT"
_SUMMARY_PROMPT_ENV: Final = "PLAUD_SYNC_SUMMARY_PROMPT"
_DEFAULT_TIMEOUT: Final = 120.0
_DROP_SAMPLES: Final = 5
_DROP_TEXT: Final = 40


def build_extractor(
    environment: Mapping[str, str],
    *,
    repo_root: Path,
    complete: Callable[[str], str] | None = None,
) -> Extractor:
    """녹취 하나를 추출 결과로 바꾸는 Extractor. 템플릿은 최초 사용 때 한 번만 읽는다."""
    template_path = _template_path(environment, repo_root)
    summary_path = _template_path(
        environment, repo_root, env_name=_SUMMARY_PROMPT_ENV, relpath=_SUMMARY_RELPATH
    )
    completer = complete if complete is not None else _live_completer(environment)
    template_cell: list[str] = []
    summary_cell: list[str] = []

    def _extract(recording: LifelogRecording) -> ExtractionOutcome:
        if completer is None:
            # 모델 호출을 구성하지 못하면 추출만 생략한다.
            return ExtractionSkipped(_NO_LLM_REASON)
        if not template_cell:
            template_cell.append(_read_template(template_path))
        outcome = extract(
            recording, template=template_cell[0], complete=_reporting_completer(completer)
        )
        if outcome.summary.strip() or recording.summary_markdown.strip():
            return outcome
        # 요약이 정말 없다 — Plaud 도 첫 추출도 주지 못했다. 요약만 한 번 더 묻는다.
        # 그 호출이 실패해도 이미 얻은 사람·장소·결정·할 일은 버리지 않는다: 추출은
        # 성공했고, 이번 폴을 실패시키면 그 결과가 사라진다.
        try:
            if not summary_cell:
                summary_cell.append(_read_template(summary_path))
            repaired = summarize(recording, template=summary_cell[0], complete=completer)
        except LifelogExtractError:
            return outcome
        return replace(outcome, summary=repaired) if repaired else outcome

    return _extract


def _reporting_completer(complete: Callable[[str], str]) -> Callable[[str], str]:
    """추출 응답만 한 번 관측한다. 요약 재시도·미설정 생략은 추출 횟수에 넣지 않는다."""
    def reported(prompt: str) -> str:
        raw = complete(prompt)
        drops = reference_drops(raw)
        report = {
            "count": len(drops),
            "omitted": max(0, len(drops) - _DROP_SAMPLES),
            "values": [
                {"field": drop.field, "value": _drop_preview(drop.value)}
                for drop in drops[:_DROP_SAMPLES]
            ],
        }
        # 0건도 남겨 빈도 계산의 분모를 보존한다. 전체 응답·요약·전사는 싣지 않는다.
        print(f"LIFELOG-REFERENCE-DROP {json.dumps(report, ensure_ascii=False)}", file=sys.stderr)
        return raw

    return reported


def _drop_preview(value: str) -> str:
    """제어·방향 문자·고립 surrogate 를 이스케이프한 뒤 표시 길이를 제한한다."""
    safe = "".join(char if char.isprintable() else ascii(char)[1:-1] for char in value)
    return safe if len(safe) <= _DROP_TEXT else safe[:_DROP_TEXT - 1] + "…"


def _template_path(
    environment: Mapping[str, str],
    repo_root: Path,
    *,
    env_name: str = _PROMPT_ENV,
    relpath: tuple[str, ...] = _TEMPLATE_RELPATH,
) -> Path:
    override = environment.get(env_name, "").strip()
    return Path(override) if override else repo_root.joinpath(*relpath)


def _read_template(path: Path) -> str:
    """템플릿 부재는 배포 결함이므로 조용히 넘기지 않고 크게 실패한다 (다음 폴에 재시도)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        raise LifelogExtractError(f"추출 프롬프트를 읽을 수 없다: {path}") from None
    _, anchor, body = raw.partition(_PROMPT_ANCHOR)
    return (body if anchor else raw).lstrip("\n")


def _live_completer(environment: Mapping[str, str]) -> Callable[[str], str] | None:
    """계정 설정의 주 모델과 폴백을 쓰는 단발 호출자."""
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
