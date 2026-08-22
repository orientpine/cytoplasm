from __future__ import annotations

import ast

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "skills" / "budget" / "scripts" / "budget_watch.py"


def _run_wrapper(home: Path, scripts: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HOME": str(home)}
    if scripts is not None:
        env["BUDGET_SCRIPTS"] = str(scripts)
    return subprocess.run(
        [sys.executable, str(_WRAPPER)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


def _plant_cli(scripts: Path) -> None:
    scripts.mkdir(parents=True)
    (scripts / "budget_cli.py").write_text("raise SystemExit(0)\n", encoding="utf-8")


def test_wrapper_ignores_the_self_skill_root(tmp_path: Path) -> None:
    # Given: an executable lookalike exists only in the account-owned self-skill root.
    _plant_cli(tmp_path / ".hermes" / "skills" / "budget" / "scripts")

    # When: the wrapper resolves its governed budget CLI.
    result = _run_wrapper(tmp_path)

    # Then: the self-authored skill is ignored rather than executed.
    assert result.returncode != 0
    assert result.stdout.strip() == "budget-watch error: budget skill is not mounted"


def test_wrapper_honors_the_scripts_env_override(tmp_path: Path) -> None:
    # Given: the governed CLI is supplied through the test/runtime override.
    scripts = tmp_path / "mounted-budget" / "scripts"
    _plant_cli(scripts)

    # When: the wrapper runs with that scripts directory.
    result = _run_wrapper(tmp_path, scripts)

    # Then: the wrapper executes the mounted CLI successfully and remains silent.
    assert result.returncode == 0
    assert result.stdout == ""


def test_wrapper_self_loads_secrets_and_propagates_them_to_the_child() -> None:
    """no-agent cron 규약: 시크릿을 자체 로드하고 자식 env 에 명시 전파한다.

    2026-08-18 실측: `BUDGET_SHEET_ID` 를 `~/.env.secrets` 에 넣었는데도 워처는 계속
    `GATE-REFUSED BUDGET_SHEET_ID가 없습니다 (fail-closed)` 로 죽었다. Hermes no-agent
    cron 은 시크릿을 os.environ 에 넣지 않으므로, 자체 로드가 없으면 설정이 있어도
    자식에게 도달하지 않는다 — 셸에서 `set -a; . ~/.env.secrets` 를 한 뒤 같은 워처를
    돌리면 rc=0 이었다. 정답 형태는 `skills/todo/scripts/todo_confirm_reaction_watch.py`
    의 `_load_env_secrets()` 다.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "skills" / "budget" / "scripts" / "budget_watch.py"
    ).read_text(encoding="utf-8")

    assert ".env.secrets" in source, (
        "워처가 ~/.env.secrets 를 자체 로드하지 않는다 — no-agent cron 에서는 "
        "os.environ 에 시크릿이 없어 설정해도 무효다"
    )

    tree = ast.parse(source)
    spawns = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "subprocess.run"
    ]
    assert spawns, "subprocess.run 호출을 찾지 못했다 — 래퍼 구조가 바뀌었나"
    for call in spawns:
        assert any(kw.arg == "env" for kw in call.keywords), (
            "자식에게 env= 를 명시 전파하지 않는다 — 규약 (b-2) 위반"
        )
