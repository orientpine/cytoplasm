"""Every release path that cuts a tag also publishes its GitHub Release note.

릴리스 = 서명 태그 + GitHub Release 노트(AGENTS.md 「공개 릴리스 규칙」). 노드는 태그만 보고
수렴하므로 노트가 빠져도 아무것도 깨지지 않는 것처럼 보인다 — 2026-10-08 v1.16.0·v1.17.0 이
태그만 남아 Latest 가 v1.15.1 에 멈췄다. 동작은 test_release_sh.py 가 실제 실행으로 보고,
이 파일은 **태그를 자르는 모든 호출부**가 같은 단계에서 노트를 게시하는지를 소스로 대조한다
(새 태그 경로가 생기면 여기서 RED 다).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_AUTOMATION: Final = Path(__file__).resolve().parents[2] / "automation"
_LIB: Final = "release_tag_lib.sh"

#: 태그를 자르지만 노트 단계를 갖지 않는 경로 — 사유가 있어야 한다.
_EXEMPT: Final = {
    "land.sh": "main 직접 push 경로다. 2026-10-07 공개 우선 전환 뒤 브랜치 보호가 main push 를 "
    "서버에서 거부하므로 태그 단계에 도달할 수 없다",
}


def _tag_cutters() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(_AUTOMATION.glob("*.sh"))
        if path.name != _LIB
        and re.search(r"^\s*ensure_signed_tag\b", path.read_text(encoding="utf-8"), re.MULTILINE)
    }


def test_every_tag_cut_is_followed_by_its_release_note() -> None:
    missing = []
    for name, source in _tag_cutters().items():
        if name in _EXEMPT:
            continue
        tag_at = re.search(r"^\s*ensure_signed_tag\b", source, re.MULTILINE)
        note_at = re.search(r"^\s*ensure_release_note\b", source[tag_at.end():], re.MULTILINE) if tag_at else None
        if note_at is None:
            missing.append(name)
    assert not missing, f"tag cut without a release note step: {missing}"


def test_the_known_release_paths_are_covered() -> None:
    assert {"release.sh", "release_complete.sh", "release-tag.sh"} <= set(_tag_cutters())


def test_exemptions_are_not_stale() -> None:
    assert set(_EXEMPT) <= set(_tag_cutters())


def test_the_note_helper_lives_only_in_the_tag_library() -> None:
    definitions = [
        path.name
        for path in _AUTOMATION.glob("*.sh")
        if re.search(r"^ensure_release_note\(\)", path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert definitions == [_LIB]
