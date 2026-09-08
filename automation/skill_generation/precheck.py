"""제작 **전** 기존 스킬 대조 — 중복 스킬을 만들기 전에 멈추는 게이트(t_0a7959e9).

소유자 지시(2026-09-04): 새 스킬을 만들거나 기존 스킬을 확장하기 전에 **전체 기존 스킬
목록**과 관련 후보의 `SKILL.md`·연결 파일을 먼저 확인하고, 이름·설명·트리거·기능·
외부효과 게이트·스크립트·테스트를 대조해 **재사용·확장·통합 가능성을 먼저** 판단한다.
기존 스킬이 있으면 신규 제작보다 그 스킬의 패치를 우선하고, 그래도 새로 만들어야 하면
차이와 재사용한 구성요소를 초안에 적는다.

`selfskill_audit.overlap` 은 **이미 만들어진** 자가 스킬을 사후 감사한다(SC-4). 같은 판정을
제작 **앞**으로 옮긴 것이 이 모듈이고, 점수 정의(`containment`·`THRESHOLD`·`MIN_SHARED`·
토크나이저)는 복사하지 않고 그 모듈에서 그대로 import 한다 — 두 벌이면 한 벌이 낡는다.

판정은 LLM 없이 결정적이다: 같은 입력이면 순서까지 같은 `Verdict` 가 나오고, 그 안에는
열거한 전체 이름(`enumerated`)과 실제로 읽은 `SKILL.md` 경로(`viewed`)가 증적으로 남는다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from automation.selfskill_audit.overlap import MIN_SHARED, THRESHOLD, containment, description_tokens
from automation.skill_generation.core import Observation
from automation.skill_mount import live_root

#: 이 패키지가 사는 트리의 스킬 루트(리포에서는 `skills/`). 노드 런타임 사본에는 없을 수 있다.
_RUNTIME_SKILLS: Final = Path(__file__).resolve().parents[2] / "skills"

#: 표시용 원문만 여기서 읽는다 — 점수 토큰의 정의는 `overlap.description_tokens` 가 소유한다.
_DESCRIPTION: Final = re.compile(r'^description:\s*"?(.*?)"?\s*$', re.MULTILINE)
_TAGS: Final = re.compile(r"tags:\s*\[(.*?)\]")
#: 명시 호출 트리거(`!recall …`)와 본문이 인용부호로 고정한 발화 트리거(“…해줘”).
_BANG: Final = re.compile(r"(?<![0-9a-zA-Z])!([a-z][a-z0-9-]{1,30})")
_QUOTED: Final = re.compile(r"“([^”\n]{2,40})”")
_GATE_WORDS: Final = ("external_effect_gate", "approval", "승인", "게이트")
_MAX_TRIGGERS: Final = 8
_MAX_MATCHES: Final = 3
SHARED_SAMPLE: Final = 6


class VerdictKind(StrEnum):
    REUSE_EXISTING = "REUSE-EXISTING"
    NEW = "NEW"


@dataclass(frozen=True, slots=True)
class SkillCard:
    """대조 대상 한 개 — 「무엇을 비교했는가」의 기계 판독 기록."""

    name: str
    root: Path
    skill_md: Path
    description: str
    tags: tuple[str, ...]
    triggers: tuple[str, ...]
    scripts: tuple[str, ...]
    has_tests: bool
    gates: tuple[str, ...]
    tokens: frozenset[str]


@dataclass(frozen=True, slots=True)
class Catalog:
    cards: tuple[SkillCard, ...]
    #: 읽을 수 없거나 description·tags 가 없어 대조에서 빠진 것 — 조용한 누락을 만들지 않는다.
    skipped: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Match:
    name: str
    score: float
    shared: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: VerdictKind
    matches: tuple[Match, ...]
    enumerated: tuple[str, ...]
    viewed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Review:
    """제작 판단 한 건의 증적 — `reviews.jsonl` 한 줄의 형식은 이 모듈이 소유한다."""

    observation: Observation
    name: str
    verdict: Verdict
    skipped: tuple[str, ...]

    def row(self) -> dict[str, object]:
        return {
            "created": self.observation.timestamp.isoformat(),
            "name": self.name,
            "pattern_hash": self.observation.pattern_hash,
            "week": self.observation.week,
            "verdict": self.verdict.kind.value,
            "enumerated": list(self.verdict.enumerated),
            "viewed": list(self.verdict.viewed),
            "skipped": list(self.skipped),
            "matches": [
                {"name": match.name, "score": match.score, "shared": list(match.shared[:SHARED_SAMPLE])}
                for match in self.verdict.matches
            ],
        }


def latest_review(reviews: Path, name: str) -> dict[str, object] | None:
    """그 이름의 마지막 판단 증적 — 없으면 없다고 답한다(지어내지 않는다)."""
    latest: dict[str, object] | None = None
    try:
        lines = reviews.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("name") == name:
            latest = row
    return latest


def default_roots(skills_root: Path) -> tuple[Path, ...]:
    """대조할 스킬 루트 — 리포/런타임 스킬, governed live 마운트, 계정 자가 스킬 루트."""
    return (_RUNTIME_SKILLS, live_root(), skills_root)


def build_catalog(roots: tuple[Path, ...]) -> Catalog:
    """루트를 순서대로 열거한다. 앞 루트가 이름을 이기고, 없는 루트는 그냥 비어 있다."""
    cards: list[SkillCard] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for root in roots:
        for directory in _skill_dirs(root):
            if directory.name in seen:
                continue
            text = _read(directory / "SKILL.md")
            tokens = description_tokens(text) if text else frozenset()
            if not tokens:
                skipped.append(f"{directory}: SKILL.md 를 읽을 수 없거나 description·tags 가 비었다")
                continue
            seen.add(directory.name)
            cards.append(_card(directory, text, tokens))
    return Catalog(tuple(cards), tuple(skipped))


def compare(candidate_text: str, candidate_name: str, cards: tuple[SkillCard, ...]) -> Verdict:
    """후보를 카탈로그 전체와 대조한다 — 겹치거나 이름을 선점하면 재사용 판정."""
    tokens = _candidate_tokens(candidate_text)
    collision = next((card.name for card in cards if card.name.casefold() == candidate_name.casefold()), None)
    ranked = sorted(
        (_match(tokens, card) for card in cards),
        key=lambda match: (match.name != collision, -match.score, match.name),
    )
    reuse = collision is not None or any(
        match.score >= THRESHOLD and len(match.shared) >= MIN_SHARED for match in ranked
    )
    return Verdict(
        VerdictKind.REUSE_EXISTING if reuse else VerdictKind.NEW,
        tuple(ranked[:_MAX_MATCHES]),
        tuple(card.name for card in cards),
        tuple(str(card.skill_md) for card in cards),
    )


def comparison_section(verdict: Verdict, cards: tuple[SkillCard, ...]) -> str:
    """신규 초안에 박는 「기존 스킬 대조」 절 — 차이와 재사용 구성요소를 남긴다."""
    nearest = verdict.matches[0] if verdict.matches else None
    ranked = " · ".join(f"`{match.name}` {match.score:.3f}" for match in verdict.matches) or "없음"
    return (
        "## 기존 스킬 대조\n\n"
        f"- 조회한 기존 스킬 {len(verdict.enumerated)}개 · 읽은 SKILL.md {len(verdict.viewed)}개"
        " (증적: `~/.hermes/skill-generation/reviews.jsonl`)\n"
        f"- 가장 가까운 기존 스킬: {ranked}\n"
        f"- 차이: 설명 낱말 겹침이 재사용 임계값({THRESHOLD}·{MIN_SHARED}낱말) 아래라"
        " 기존 스킬의 확장으로 흡수되지 않는다. 소유자가 이 한 줄을 실제 차이로 고쳐 적는다.\n"
        f"- 재사용 구성요소: {_reusable(nearest, cards)}\n"
    )


def _reusable(nearest: Match | None, cards: tuple[SkillCard, ...]) -> str:
    card = next((card for card in cards if nearest is not None and card.name == nearest.name), None)
    if card is None or nearest is None or not nearest.shared:
        return "없음"
    parts = [f"`{card.name}` 의 공통 낱말 {'·'.join(nearest.shared[:SHARED_SAMPLE])}"]
    if card.scripts:
        parts.append(f"스크립트 `{'`, `'.join(card.scripts)}`")
    if card.triggers:
        parts.append(f"트리거 `{'`, `'.join(card.triggers)}`")
    if card.gates:
        parts.append(f"게이트 {'·'.join(card.gates)}")
    if card.has_tests:
        parts.append("기존 회귀 있음")
    return ", ".join(parts)


def _match(tokens: frozenset[str], card: SkillCard) -> Match:
    score, shared = containment(tokens, card.tokens)
    return Match(card.name, round(score, 3), shared)


def _candidate_tokens(text: str) -> frozenset[str]:
    """후보 문장을 description 한 줄로 감싸 **같은 토크나이저**를 태운다(정의 복사 금지)."""
    return description_tokens(f'description: "{" ".join(text.split())}"')


def _card(directory: Path, text: str, tokens: frozenset[str]) -> SkillCard:
    scripts = directory / "scripts"
    return SkillCard(
        name=directory.name,
        root=directory.parent,
        skill_md=directory / "SKILL.md",
        description=_first(_DESCRIPTION, text),
        tags=tuple(tag.strip() for tag in _first(_TAGS, text).split(",") if tag.strip()),
        triggers=_triggers(text),
        scripts=tuple(sorted(path.name for path in scripts.glob("*.py"))) if scripts.is_dir() else (),
        has_tests=_has_tests(directory),
        gates=tuple(word for word in _GATE_WORDS if word in text),
        tokens=tokens,
    )


def _triggers(text: str) -> tuple[str, ...]:
    found = {f"!{name}" for name in _BANG.findall(text)}
    found.update(phrase.strip() for phrase in _QUOTED.findall(text))
    return tuple(sorted(found))[:_MAX_TRIGGERS]


def _has_tests(directory: Path) -> bool:
    """리포 루트에서 열거했을 때만 참 — 런타임 사본에는 tests 트리가 없다."""
    unit = directory.parent.parent / "tests" / "unit"
    return unit.is_dir() and any(unit.glob(f"test_{directory.name.replace('-', '_')}*.py"))


def _first(pattern: re.Pattern[str], text: str) -> str:
    matched = pattern.search(text)
    return matched.group(1).strip() if matched else ""


def _skill_dirs(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    try:
        return tuple(sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")))
    except OSError:
        return ()


def _read(skill_md: Path) -> str:
    try:
        return skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
