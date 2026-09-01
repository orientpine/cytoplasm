"""워처가 live 마운트에서 import 하는 모듈 집합 — 배포 순서의 선행 조건.

no-agent 워처 중 일부는 `/srv/autophagy-skills/live/<skill>/scripts` 를 `sys.path` 에
넣고 그 안의 모듈을 **import** 한다(대개는 subprocess 로만 부른다). 그런 워처는 스킬
마운트보다 먼저 배포되면 매 틱 기동 즉시 ImportError 로 죽고, 승인 ✅ 를 아무도
소비하지 않는 침묵이 된다 — 2026-08-21 repair 승인 워처가 5일간 5,329회 그렇게 죽었다.

순서를 산문으로 적어 두는 것은 이 부채가 처음 생긴 방식이다. 필요한 모듈 이름을
**워처 소스에서 도출**해 배포 진입점이 마운트를 선검사할 수 있게 한다 — 새 import 가
생기면 목록도 따라 바뀌므로 등록을 잊는 실패 모드가 없다.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

#: 이름만 문자열로 넘기는 지연 import — 정적 import 와 똑같이 그 틱을 죽인다.
_DYNAMIC_IMPORTERS = frozenset({"__import__", "import_module"})


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # 상대 import 는 패키지 안을 가리키므로 live 마운트의 평면 모듈이 아니다.
            if node.level == 0 and node.module:
                names.add(node.module.partition(".")[0])
        elif isinstance(node, ast.Call) and _callee(node.func) in _DYNAMIC_IMPORTERS:
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value.partition(".")[0])
    return names


def required_live_modules(watcher: Path, scripts_dir: Path) -> tuple[str, ...]:
    """`watcher` 가 `scripts_dir` 의 모듈 중 무엇을 import 하는지 정렬해 돌려준다.

    교집합을 쓰므로 표준 라이브러리와 저장소 런타임(`automation.*`)은 저절로 빠진다 —
    live 마운트가 공급하는 것은 그 디렉터리의 평면 모듈뿐이기 때문이다.
    """
    local = {path.stem for path in scripts_dir.glob("*.py")} - {watcher.stem}
    tree = ast.parse(watcher.read_text(encoding="utf-8"), filename=str(watcher))
    return tuple(sorted(_imported_names(tree) & local))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="live-mount-preflight")
    _ = parser.add_argument("--watcher", type=Path, required=True)
    _ = parser.add_argument("--scripts-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    for name in required_live_modules(args.watcher, args.scripts_dir):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
