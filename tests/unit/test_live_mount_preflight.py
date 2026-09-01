"""워처의 live 마운트 선행 의존을 소스에서 도출한다.

`skills/todo/scripts/todo_confirm_reaction_watch.py` 는 live 스킬 경로를 `sys.path` 에
넣고 `todo_execution_reconcile` 을 import 한다. 마운트 전에 워처만 배포하면 매 틱
ImportError 로 죽는다 — 그 순서가 어디에도 코드로 없었다(2026-08-27 후속 과제).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from automation.live_mount_preflight import required_live_modules

_REPO = Path(__file__).resolve().parents[2]


def test_the_todo_watcher_declares_its_live_imports() -> None:
    required = required_live_modules(
        _REPO / "skills/todo/scripts/todo_confirm_reaction_watch.py",
        _REPO / "skills/todo/scripts",
    )
    # 이 모듈이 빠진 채 워처만 배포되면 매 틱 죽는다 — 이 순서가 이 가드의 존재 이유다.
    assert "todo_execution_reconcile" in required
    assert "todo_approval" in required
    # 표준 라이브러리와 저장소 런타임은 live 마운트가 공급하지 않는다.
    assert not {"os", "sys", "importlib", "automation"} & set(required)


def test_dynamic_imports_count_too(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("thing_static", "thing_dynamic", "thing_unused"):
        _ = (scripts / f"{name}.py").write_text("", encoding="utf-8")
    watcher = scripts / "thing_watch.py"
    _ = watcher.write_text(
        textwrap.dedent(
            """
            import importlib
            import os
            from thing_static import helper

            def run():
                return importlib.import_module("thing_dynamic"), helper, os
            """
        ),
        encoding="utf-8",
    )
    # 틱 0 에 죽는 것은 정적 import 지만, 동적 import 도 그 틱을 죽인다 — 둘 다 요구한다.
    assert required_live_modules(watcher, scripts) == ("thing_dynamic", "thing_static")


def test_a_watcher_with_no_live_imports_requires_nothing(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _ = (scripts / "neighbour.py").write_text("", encoding="utf-8")
    watcher = scripts / "plain_watch.py"
    _ = watcher.write_text("import json\nimport subprocess\n", encoding="utf-8")
    assert required_live_modules(watcher, scripts) == ()


def test_the_todo_deploy_script_checks_the_mount_before_it_pushes() -> None:
    """순서가 코드로 강제되는가 — 산문으로 적어 두는 것이 이 부채가 생긴 방식이다."""
    text = (_REPO / "skills/todo/deploy.sh").read_text(encoding="utf-8")
    assert "live_mount_preflight.py" in text, "마운트 선검사가 없다"
    assert "exit 5" in text, "선검사가 fail-closed 로 끝나지 않는다"
    assert text.index("live_mount_preflight.py") < text.index('push_file "$repo_root'), (
        "선검사가 push 뒤에 있다 — 그러면 이미 죽은 워처가 올라간 뒤에 막는다"
    )


def test_the_guard_checks_the_path_the_watcher_will_actually_use() -> None:
    """가드가 보는 경로와 워처가 import 하는 경로가 갈라지면 가드는 아무것도 지키지 않는다."""
    watcher = (_REPO / "skills/todo/scripts/todo_confirm_reaction_watch.py").read_text(
        encoding="utf-8"
    )
    deploy = (_REPO / "skills/todo/deploy.sh").read_text(encoding="utf-8")
    live = next(
        line.split('"')[1]
        for line in watcher.splitlines()
        if line.startswith("_LIVE_SCRIPTS")
    )
    assert f"live_scripts='{live}'" in deploy, f"deploy.sh 가 {live} 를 보지 않는다"
