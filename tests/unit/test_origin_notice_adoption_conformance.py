"""Conformance pin for the result-notice routing rule (AGENTS.md 「결과 통지 원채널 스레드 규칙」).

Owner instruction 2026-08-23: every approval-gated skill sends its RESULT
notices (executed / cancelled / expired) to the origin-channel thread through
the ONE shared implementation ``automation/interop/origin_notice.py``, or names
— with a reason — why it must not. Prose alone does not survive the next skill;
this test makes a new approval producer fail the build until it either adopts
the helper or registers an exemption here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "tests" / "unit"))

from approval_conformance_inventory import APPROVAL_PRODUCERS  # noqa: E402

_RESULT_NOTICE_EXEMPT: Final[dict[str, str]] = {
    "patent-prep": (
        "특허 민감 — 승인 요청과 완료 링크는 소유자 DM 전용이 SKILL.md의 명시 규칙이라 "
        "원 채널 스레드로 결과를 내보내지 않는다 (2026-08-23 판정)."
    ),
    "wiki": (
        "본문·제목은 소유자 DM 밖 유출 금지(SKILL.md)이고 지시 표면 자체가 DM 전용이라 "
        "원 채널 스레드 통지가 성립하지 않는다 (2026-08-23 판정)."
    ),
}

_THREAD_API_ALLOWED: Final[dict[str, str]] = {
    "skills/meeting/plugin/__init__.py": (
        "게이트웨이 플러그인 프로세스는 INTEROP_RUNTIME 경로를 보장받지 못해 앵커 규칙"
        "(지시 메시지 스레드 우선, 400=재사용)을 같은 의미로 자체 구현한다."
    ),
}


def _governed_skills() -> frozenset[str]:
    return frozenset(
        surface.split("/")[1] for surface in APPROVAL_PRODUCERS if surface.startswith("skills/")
    )


def _adopts_origin_notice(skill: str) -> bool:
    scripts = _REPO / "skills" / skill / "scripts"
    return any("origin_notice" in path.read_text(encoding="utf-8") for path in scripts.glob("*.py"))


def test_every_approval_gated_skill_routes_results_through_origin_notice() -> None:
    failures = [
        f"skills/{skill}: 결과 통지가 automation.interop.origin_notice를 거치지 않고 "
        "_RESULT_NOTICE_EXEMPT에도 없다 (AGENTS.md 「결과 통지 원채널 스레드 규칙」)"
        for skill in sorted(_governed_skills())
        if skill not in _RESULT_NOTICE_EXEMPT and not _adopts_origin_notice(skill)
    ]
    assert not failures, "\n".join(failures)


def test_result_notice_exemptions_are_not_stale() -> None:
    governed = _governed_skills()
    stale = sorted(skill for skill in _RESULT_NOTICE_EXEMPT if skill not in governed)
    adopted_anyway = sorted(skill for skill in _RESULT_NOTICE_EXEMPT if _adopts_origin_notice(skill))
    assert not stale, f"exempt skills without an approval producer: {stale}"
    assert not adopted_anyway, f"exempt skills already adopt origin_notice — drop the exemption: {adopted_anyway}"
    assert all(reason.strip() for reason in _RESULT_NOTICE_EXEMPT.values())


def test_thread_creation_lives_only_in_the_shared_helper() -> None:
    offenders = sorted(
        str(path.relative_to(_REPO))
        for path in (_REPO / "skills").rglob("*.py")
        if "vendor" not in path.parts
        and "/threads" in path.read_text(encoding="utf-8", errors="ignore")
        and str(path.relative_to(_REPO)) not in _THREAD_API_ALLOWED
    )
    assert not offenders, (
        "스레드 생성은 automation/interop/origin_notice.py 하나만 구현한다 — 사본 금지: "
        + ", ".join(offenders)
    )
    assert all(reason.strip() for reason in _THREAD_API_ALLOWED.values())
