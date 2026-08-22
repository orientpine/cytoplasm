"""`~/.hermes/scripts/` 로 배포되는 **모든** 워처가 규약 (b)·(b-2)를 지키는가 — 인벤토리 검사.

2026-08-18 실측 배경: `budget-watch` 는 `BUDGET_SHEET_ID` 를 `~/.env.secrets` 에 넣은
뒤에도 계속 `GATE-REFUSED ... 없습니다` 로 죽었다. 값이 틀린 게 아니라 **전달 경로가
없었다** — Hermes no-agent cron 은 시크릿을 `os.environ` 에 넣지 않으므로, 래퍼가
스스로 읽지 않으면 설정이 있어도 자식에게 도달하지 않는다. 그때 고친 것은 budget 한
파일이고 회귀도 그 한 파일만 봤다. 이 검사는 그 질문을 **배포되는 워처 전체**로 넓힌다.

`env=` 는 있는지만 보지 않는다. `env=environment` 처럼 기본값이 `None` 인 매개변수를
그대로 넘기면 문법상 명시 전파처럼 보이지만 런타임에는 상속 폴백이며, 규약 (b-2)가
금지하는 것이 정확히 그 상속 의존이다 — 그래서 값까지 본다.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PUSH = re.compile(
    r"push_file\s+\"?\$repo_root/(?P<source>[^\"'\s]+)\"?\s*(?:\\\s*)?[\"']\.hermes/scripts/",
    re.M,
)
_SPAWNS = frozenset({"subprocess.run", "subprocess.Popen"})
#: cron 이 직접 돌리는 엔트리포인트만 자체 로드 의무를 진다 — 판정은 `__main__` 가드다
#: (test_watcher_deploy_coverage 와 같은 프로브). 그 엔트리포인트가 import 하는
#: 헬퍼 모듈은 부모가 이미 로드한 os.environ 을 그대로 쓴다.
_MAIN_GUARD = re.compile(r"^if __name__ == ['\"]__main__['\"]:", re.M)

#: 시크릿이 필요 없다고 **증명된** 워처만 여기 적는다. 사유 없는 등재는 조용한 통과다.
_NO_SECRETS_NEEDED: dict[str, str] = {}


def _deployed_watchers() -> list[Path]:
    sources: set[Path] = set()
    for script in sorted(_REPO.glob("**/deploy.sh")):
        if "vendor" in script.parts:
            continue
        for match in _PUSH.finditer(script.read_text(encoding="utf-8")):
            candidate = _REPO / match.group("source")
            if candidate.suffix == ".py" and candidate.is_file():
                sources.add(candidate)
    assert sources, "배포되는 워처를 하나도 찾지 못했다 — deploy.sh 형식이 바뀌었나"
    return sorted(sources)


def _relative(path: Path) -> str:
    return path.relative_to(_REPO).as_posix()


def _none_defaulted_parameters(tree: ast.AST) -> set[str]:
    """`def f(..., env=None)` 처럼 기본값이 None 인 매개변수 이름들."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        arguments = node.args
        positional = arguments.posonlyargs + arguments.args
        for argument, default in zip(positional[len(positional) - len(arguments.defaults):],
                                     arguments.defaults, strict=True):
            if isinstance(default, ast.Constant) and default.value is None:
                names.add(argument.arg)
        for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
            if isinstance(default, ast.Constant) and default.value is None:
                names.add(argument.arg)
    return names

def _spawn_names(tree: ast.AST) -> frozenset[str]:
    """`subprocess.run` 과, `from subprocess import run as X` 로 묶인 지역 별칭까지."""
    aliases = set(_SPAWNS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            aliases |= {
                name.asname or name.name
                for name in node.names
                if name.name in {"run", "Popen"}
            }
    return frozenset(aliases)


@pytest.mark.parametrize("watcher", _deployed_watchers(), ids=_relative)
def test_deployed_watcher_self_loads_the_secrets_file(watcher: Path) -> None:
    # Given: a watcher that Hermes runs as a no-agent cron job.
    name = _relative(watcher)
    source = watcher.read_text(encoding="utf-8")
    if not _MAIN_GUARD.search(source):
        pytest.skip(f"{name} 는 cron 엔트리포인트가 아니라 헬퍼 모듈이다")
    if name in _NO_SECRETS_NEEDED:
        pytest.skip(f"면제: {_NO_SECRETS_NEEDED[name]}")

    # Then: it reads ~/.env.secrets itself, because cron hands it none.
    assert ".env.secrets" in source, (
        f"{name} 가 ~/.env.secrets 를 자체 로드하지 않는다 — no-agent cron 에서는 "
        "설정을 넣어도 조용히 무시된다(2026-08-18 budget-watch 선례)"
    )


@pytest.mark.parametrize("watcher", _deployed_watchers(), ids=_relative)
def test_deployed_watcher_states_the_child_environment_explicitly(watcher: Path) -> None:
    # Given: every subprocess this watcher spawns.
    # Given: every subprocess this watcher spawns, however `subprocess` was imported.
    tree = ast.parse(watcher.read_text(encoding="utf-8"))
    inherited = _none_defaulted_parameters(tree)
    spawns = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) in _spawn_names(tree)
    ]

    # Then: each one receives an environment that cannot silently fall back to inheritance.
    for call in spawns:
        keyword = next((item for item in call.keywords if item.arg == "env"), None)
        assert keyword is not None, (
            f"{_relative(watcher)}:{call.lineno} 가 자식에게 env= 를 넘기지 않는다 — 규약 (b-2) 위반"
        )
        value = ast.unparse(keyword.value)
        assert value != "None", f"{_relative(watcher)}:{call.lineno} 의 env= 가 None 이다"
        assert not (isinstance(keyword.value, ast.Name) and keyword.value.id in inherited), (
            f"{_relative(watcher)}:{call.lineno} 의 env={value} 는 기본값이 None 인 매개변수라 "
            "런타임에는 상속 폴백이다 — 규약 (b-2)가 금지하는 바로 그 형태다"
        )
