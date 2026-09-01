"""과제별×년도별 다중 시트 레지스트리 (fail-closed 파싱/해석).

레지스트리 파일이 없으면 ``None`` — 레거시 단일 ``BUDGET_SHEET_ID`` 모드가
그대로 유지된다. 파일이 있으면 그것이 유일한 소스이며, ``BUDGET_SHEET_ID``가
레지스트리에 등재되지 않은 채 남아 있으면 조용한 추적 누락이므로 거부한다.
실제 sheet ID는 repo 밖(`~/.hermes/budget/sheets.json`)에만 둔다
(`configs/budget-sheets.example.json` 참조).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import budget_gate

DEFAULT_REGISTRY_PATH: Final = "~/.hermes/budget/sheets.json"
REGISTRY_FILE_ENV: Final = "BUDGET_SHEETS_FILE"
_YEAR_RE: Final = re.compile(r"^\d{4}$")
_FORBIDDEN_IN_PROJECT: Final = ("/", ":")


@dataclass(frozen=True, slots=True)
class SheetRef:
    project: str
    year: int
    sheet_id: str

    @property
    def sheet_key(self) -> str:
        return f"{self.project}/{self.year}"


def _reject(reason: str) -> budget_gate.GateError:
    return budget_gate.GateError(f"budget 레지스트리 오류: {reason} (fail-closed)", 3)


def parse_registry(text: str) -> tuple[SheetRef, ...]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise _reject(f"JSON 파싱 실패 — {error}") from None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise _reject("version=1 레지스트리가 아님")
    projects = payload.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise _reject("projects가 비어 있음")
    refs: list[SheetRef] = []
    seen_ids: dict[str, str] = {}
    for project, years in projects.items():
        if not isinstance(project, str) or not project.strip():
            raise _reject("과제명이 비어 있음")
        if any(ch in project for ch in _FORBIDDEN_IN_PROJECT):
            raise _reject(f"과제명에 금지 문자(/ 또는 :) 포함: {project!r}")
        if not isinstance(years, dict) or not years:
            raise _reject(f"{project!r}의 값이 년도→시트 매핑이 아님")
        for year_key, sheet_id in years.items():
            if not isinstance(year_key, str) or not _YEAR_RE.fullmatch(year_key):
                raise _reject(f"{project!r}의 년도 키가 4자리 년도가 아님: {year_key!r}")
            if not isinstance(sheet_id, str) or not sheet_id.strip():
                raise _reject(f"{project!r}/{year_key}의 sheet id가 비어 있음")
            ref = SheetRef(project=project, year=int(year_key), sheet_id=sheet_id.strip())
            if ref.sheet_id in seen_ids:
                raise _reject(
                    f"sheet id 중복: {seen_ids[ref.sheet_id]} 와 {ref.sheet_key}"
                )
            seen_ids[ref.sheet_id] = ref.sheet_key
            refs.append(ref)
    return tuple(sorted(refs, key=lambda ref: (ref.project, ref.year)))


def registry_path() -> Path:
    raw = os.environ.get(REGISTRY_FILE_ENV, "").strip() or DEFAULT_REGISTRY_PATH
    return Path(raw).expanduser()


def load_registry(path: Path | None = None) -> tuple[SheetRef, ...] | None:
    target = registry_path() if path is None else path
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _reject(f"레지스트리 읽기 실패 — {error}") from None
    return parse_registry(text)


def active_refs() -> tuple[SheetRef, ...] | None:
    refs = load_registry()
    if refs is None:
        return None
    legacy = os.environ.get("BUDGET_SHEET_ID", "").strip()
    if legacy and all(ref.sheet_id != legacy for ref in refs):
        raise _reject(
            "BUDGET_SHEET_ID가 레지스트리에 등재되지 않음 — "
            "레지스트리에 추가하거나 env에서 제거"
        )
    return refs


def _known_projects(refs: tuple[SheetRef, ...]) -> str:
    return ", ".join(sorted({ref.project for ref in refs}))


def select(refs: tuple[SheetRef, ...], *, project: str, year: int) -> SheetRef:
    candidates = [ref for ref in refs if not project or ref.project == project]
    if project and not candidates:
        raise budget_gate.GateError(
            f"알 수 없는 과제: {project} (알려진 과제: {_known_projects(refs)})", 2
        )
    if year:
        scoped = [ref for ref in candidates if ref.year == year]
        if not scoped:
            known_years = ", ".join(str(ref.year) for ref in candidates)
            raise budget_gate.GateError(
                f"등록되지 않은 년도: {year} (알려진 년도: {known_years})", 2
            )
        candidates = scoped
    if len(candidates) == 1:
        return candidates[0]
    if not project:
        raise budget_gate.GateError(
            f"--project가 필요합니다 (알려진 과제: {_known_projects(refs)})", 2
        )
    return max(candidates, key=lambda ref: ref.year)
