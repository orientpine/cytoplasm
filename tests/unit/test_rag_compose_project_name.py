"""RAG 스택 compose 프로젝트 이름은 하나다.

유닛은 ``COMPOSE_PROJECT_NAME=personal_rag``(밑줄)를 강제하는데 compose 파일이 다른
이름을 선언하면, 유닛을 거치지 않는 손 실행이 **다른 프로젝트**를 계산해 이미 돌고 있는
컨테이너를 자기 것으로 보지 못한다. 2026-08-22 실측: 그 상태로 MCP 활성화가 두 번
``Bind for 0.0.0.0:8765 failed: port is already allocated`` 로 죽었고 볼륨은
``created for project "personal_rag" (expected "personal-rag")`` 경고를 냈다.

여기서 고정하는 값은 산문이 아니라 docker compose 가 소비하는 프로젝트 이름이다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_COMPOSE: Final = _REPO / "configs" / "rag" / "compose.yaml"
_UNIT: Final = _REPO / "configs" / "rag" / "personal-rag.service"


def _compose_project_name() -> str:
    match = re.search(r"(?m)^name:[ \t]*(\S+)[ \t]*$", _COMPOSE.read_text(encoding="utf-8"))
    assert match is not None, "compose.yaml must declare a top-level project name"
    return match.group(1)


def _unit_project_name() -> str:
    match = re.search(
        r"(?m)^Environment=COMPOSE_PROJECT_NAME=(\S+)[ \t]*$", _UNIT.read_text(encoding="utf-8")
    )
    assert match is not None, "the unit must pin COMPOSE_PROJECT_NAME"
    return match.group(1)


def test_compose_declares_the_same_project_name_as_the_unit() -> None:
    assert _compose_project_name() == _unit_project_name()


def test_named_volumes_belong_to_that_project() -> None:
    """볼륨 이름이 프로젝트 접두사와 어긋나면 compose 가 소유권 경고를 낸다."""
    project = _compose_project_name()
    text = _COMPOSE.read_text(encoding="utf-8")
    volume_names = re.findall(r"(?m)^ {4}name:[ \t]*(\S+)[ \t]*$", text)
    assert volume_names, "expected explicitly named volumes"
    mismatched = [name for name in volume_names if not name.startswith(f"{project}_")]
    assert not mismatched, f"volumes outside project {project!r}: {mismatched}"
