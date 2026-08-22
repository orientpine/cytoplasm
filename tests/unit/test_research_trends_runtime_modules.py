"""research_trends 가 런타임에 import 하는 자기 패키지 모듈은 배포에도 실려야 한다 — 회귀 고정.

2026-08-18 실측: `topics_import.py` 를 새로 분리하면서 `research_trends.py` 가 그것을
`automation.research_trends.topics_import` 로 import 하게 됐는데, `deploy.sh` 의 런타임
스트림 목록에는 넣지 않았다. 배포 직후 노드에서:

    ModuleNotFoundError: No module named 'automation.research_trends.topics_import'

`automation.*` 철자는 불변 릴리스가 origin/main 으로 수렴해야 풀리는데, 그 수렴은 이 배포와
동기가 아니다 — 실측 시점에 릴리스는 `c6543ce5`, origin/main 은 `14868a66` 로 3커밋 뒤처져
있었다. 즉 rc=0 이던 워처가 배포 때문에 rc=1 로 회귀했다.

저장소 표준 해법은 `automation/reminder_poller/poll_reminders.py` 가 이미 쓰는 형태다 —
헬퍼를 런타임 디렉터리에 **flat** 으로 배포하고 `try/except ImportError` 로 폴백한다.
그러면 릴리스 수렴 상태와 무관하게 동작한다.

판정은 정규식이 아니라 `ast` 로 한다 — `if TYPE_CHECKING:` 아래의 import 는 런타임에
실행되지 않으므로 배포 대상이 아니고, 정규식은 그 둘을 구분하지 못한다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG = _REPO_ROOT / "automation" / "research_trends"
_WATCHER = _PKG / "research_trends.py"
_DEPLOY = _PKG / "deploy.sh"

_SELF_PKG = "automation.research_trends"
#: `deploy_archive_stream <root> "<dir>" a.py b.py ...`
_STREAM = re.compile(r'deploy_archive_stream[^\n]*?research_trends"((?:\s+[\w.]+\.py)+)')


def _is_type_checking_guard(node: ast.stmt) -> bool:
    return isinstance(node, ast.If) and ast.unparse(node.test).strip() == "TYPE_CHECKING"


def _runtime_self_imports(source: str) -> set[str]:
    """Modules of our own package imported at RUNTIME (TYPE_CHECKING bodies excluded)."""
    tree = ast.parse(source)
    skip: set[int] = set()
    for node in ast.walk(tree):
        if _is_type_checking_guard(node):
            assert isinstance(node, ast.If)
            skip.update(id(child) for child in ast.walk(node) if isinstance(child, ast.ImportFrom))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or id(node) in skip:
            continue
        if node.module and node.module.startswith(f"{_SELF_PKG}."):
            found.add(node.module.removeprefix(f"{_SELF_PKG}."))
    return found


def _flat_fallback_imports(source: str) -> set[str]:
    """Bare-module imports — the flat runtime-dir spelling."""
    return {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and "." not in node.module
    }


def _streamed_modules() -> set[str]:
    match = _STREAM.search(_DEPLOY.read_text(encoding="utf-8"))
    assert match is not None, "deploy_archive_stream 호출을 찾지 못했다 — 배포 형태가 바뀌었나"
    return set(match.group(1).split())


def test_every_runtime_self_import_is_shipped_to_the_runtime_dir() -> None:
    imported = _runtime_self_imports(_WATCHER.read_text(encoding="utf-8"))
    assert imported, "런타임 self-import 를 하나도 못 찾았다 — ast 판별이 잘못됐다"
    missing = sorted(f"{name}.py" for name in imported if f"{name}.py" not in _streamed_modules())
    assert not missing, (
        "research_trends.py 가 런타임에 import 하는데 deploy.sh 의 런타임 스트림에 없는 모듈: "
        f"{missing} — 배포 직후 ModuleNotFoundError 로 워처가 죽는다"
    )


def test_runtime_self_imports_fall_back_to_the_flat_layout() -> None:
    """릴리스 수렴에 기대지 않는다 — flat 폴백이 있어야 배포 즉시 동작한다."""
    source = _WATCHER.read_text(encoding="utf-8")
    fallbacks = _flat_fallback_imports(source)
    missing = sorted(_runtime_self_imports(source) - fallbacks)
    assert not missing, (
        f"flat 폴백(`from <name> import ...`)이 없는 런타임 self-import: {missing} — "
        "불변 릴리스가 낡아 있으면 그대로 죽는다"
    )
