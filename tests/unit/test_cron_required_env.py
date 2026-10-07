"""cron 스크립트가 기본값 없이 읽는 환경값은 그 cron 선언의 `env=` 에 있어야 한다.

doctor 는 그 선언을 보고 노드 계정에 값이 있는지 확인한다(`automation/doctor/cron_env.py`).
선언이 빠지면 doctor 도 모른다 — 2026-09-04 부터 일일 지출 보고가 `COST_REPORT_SOFT_CAP`
없이 29일 동안 시작하자마자 죽은 모양이 그것이다. 그래서 선언 누락을 여기서 막는다.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from automation.deploy_declarations import all_declarations

_REPO: Final = Path(__file__).resolve().parents[2]


def _required_reads(source: Path) -> frozenset[str]:
    """`os.environ["X"]` 로 읽는 이름 — 없으면 KeyError 로 죽는 읽기만 센다(대입·.get 은 아니다)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            names.add(node.slice.value)
    return frozenset(names)


def test_every_cron_script_declares_the_environment_it_cannot_start_without() -> None:
    crons = [declaration for declaration in all_declarations(_REPO) if declaration.kind == "cron"]
    assert crons, "cron 선언이 하나도 없다 — 검사가 아무것도 보지 않는다"

    undeclared = {
        f"{declaration.owner}/deploy-manifest.txt: {declaration.destination}": sorted(missing)
        for declaration in crons
        if (missing := _required_reads(_REPO / declaration.source)
            - frozenset(filter(None, declaration.attr("env").split(","))))
    }

    assert undeclared == {}, (
        "기본값 없이 읽는 환경값이 cron 선언 env= 에 없다 — 그 행에 ;env=<이름> 을 더해야 "
        f"doctor 가 노드에 값이 없을 때 첫 실행 전에 알린다: {undeclared}"
    )

