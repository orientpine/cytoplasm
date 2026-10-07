"""skill-generation 런타임 사본이 선언으로만 정해지는지 본다(RCB todo 24, GAP-3).

새 파일인 이유: `test_skill_generation_deploy.py` 는 배포된 런타임에서 CLI·플러그인이 실제로
도는지를 보고, 여기는 **선언 자체**가 닫혀 있는지와 배포기가 그 선언대로만 움직이는지를 본다.
2026-09-18 수정은 배포기에 패키지를 더했지만 선언이 없어 릴리스 판정이 런타임의 결손을 몰랐고,
배포기는 다시 돌지 않았다 — 노드 런타임에 `selfskill_audit` 이 없어 precheck import 가 죽었다.

닫힘 규칙: 플러그인·생성기가 import 하는 `automation.*` 모듈은 전부 이 패키지의 v2 선언이 런타임
(`.hermes/skill-generation/runtime/`) 아래로 싣는 파일로 풀려야 한다. 게이트웨이가 먼저 묶는 옛
부분 패키지(`~/.hermes/interop_runtime/automation`)의 내용은 노드마다 다르고 여기서 선언하지
않으므로, 런타임은 그것 없이 스스로 닫혀야 한다. `from M import n` 의 `n` 은 하위 모듈 파일이거나
`M` 의 최상위 이름이어야 한다 — 어느 쪽도 아니면 게이트웨이 훅이 ImportError 로 죽는다.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path
from typing import Final

from automation.deploy_declarations import Declaration, parse_declaration_file

_REPO: Final = Path(__file__).resolve().parents[2]
_PACKAGE: Final = "automation/skill_generation"
_MANIFEST: Final = f"{_PACKAGE}/deploy-manifest.txt"
_DEPLOY: Final = _REPO / _PACKAGE / "deploy.sh"
_RUNTIME: Final = ".hermes/skill-generation/runtime"
_INIT: Final = f"{_RUNTIME}/automation/__init__.py"
_ROOTS: Final = (f"{_PACKAGE}/plugin/__init__.py", f"{_PACKAGE}/cli.py", f"{_PACKAGE}/service.py")


def _declarations(root: Path) -> tuple[Declaration, ...]:
    return parse_declaration_file(_MANIFEST, (root / _MANIFEST).read_text(encoding="utf-8"))


def _tracked(root: Path, source: str) -> tuple[str, ...]:
    listed = subprocess.run(
        ("git", "-C", str(root / source), "ls-files", "-z"),
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout
    return tuple(name for name in listed.split("\0") if name.endswith(".py") and not name.startswith("cron/"))


def shipped(root: Path, declarations: tuple[Declaration, ...]) -> dict[str, str]:
    """선언된 런타임 목적지(홈 상대) → 저장소 상대 소스. 트리는 배포 헬퍼의 기본 선택과 같다."""
    files: dict[str, str] = {}
    for row in declarations:
        if row.legacy or not row.destination.startswith(f"{_RUNTIME}/"):
            continue
        if row.kind == "file":
            files[row.destination] = row.source
        elif row.kind == "tree":
            for name in _tracked(root, row.source):
                files[f"{row.destination}/{name}"] = f"{row.source}/{name}"
    return files


def _top_names(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    pending: list[ast.stmt] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(node, (ast.If, ast.Try)):
            pending.extend(node.body + node.orelse + getattr(node, "finalbody", []))
            pending.extend(s for h in getattr(node, "handlers", []) for s in h.body)
    return frozenset(names)


def _module_file(root: Path, dotted: str) -> str | None:
    base = "/".join(dotted.split("."))
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if (root / candidate).is_file():
            return candidate
    return None


def closure_problems(root: Path, declarations: tuple[Declaration, ...]) -> tuple[str, ...]:
    """닫힘의 결손: 풀리지 않는 import, 그리고 선언이 런타임에 싣지 않는 파일."""
    ship = set(shipped(root, declarations).values())
    problems: list[str] = []
    pending, visited = list(_ROOTS), set[str]()
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        if relative not in ship:
            problems.append(f"not shipped: {relative}")
        tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            modules: list[tuple[str, tuple[str, ...]]] = []
            if isinstance(node, ast.Import):
                modules = [(alias.name, ()) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [(node.module, tuple(alias.name for alias in node.names))]
            for module, members in modules:
                if not module.startswith("automation."):
                    continue
                parts = module.split(".")
                for depth in range(2, len(parts) + 1):
                    found = _module_file(root, ".".join(parts[:depth]))
                    if found is None:
                        problems.append(f"unresolved: {module} in {relative}")
                        break
                    pending.append(found)
                else:
                    host = _module_file(root, module) or ""
                    for member in members:
                        sub = _module_file(root, f"{module}.{member}")
                        if sub is not None:
                            pending.append(sub)
                        elif member not in _top_names(ast.parse((root / host).read_text(encoding="utf-8"))):
                            problems.append(f"unresolved: {module}.{member} in {relative}")
    return tuple(sorted(set(problems)))


def _copy_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    sources = {row.source for row in _declarations(_REPO)} | {_MANIFEST}
    for source in sources:
        origin = _REPO / source
        if origin.is_dir():
            shutil.copytree(origin, root / source, ignore=shutil.ignore_patterns("__pycache__"),
                            dirs_exist_ok=True)
        else:
            (root / source).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, root / source)
    for command in (("init", "-q"), ("add", "-A")):
        subprocess.run(("git", "-C", str(root), *command), check=True, capture_output=True, timeout=30)
    return root


def test_declared_rows_cover_the_runtime_import_closure() -> None:
    declarations = _declarations(_REPO)
    assert {row.destination for row in declarations if not row.legacy} >= {
        f"{_RUNTIME}/automation/skill_generation",
        f"{_RUNTIME}/automation/selfskill_audit",
    }
    assert all(row.attr("activation") == "gateway" for row in declarations if not row.legacy)
    assert closure_problems(_REPO, declarations) == ()


def test_an_undeclared_runtime_import_breaks_the_build(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path)
    assert closure_problems(root, _declarations(root)) == ()
    service = root / _PACKAGE / "service.py"
    _ = service.write_text(
        service.read_text(encoding="utf-8") + "\nfrom automation.selfskill_audit import extra\n",
        encoding="utf-8",
    )
    assert closure_problems(root, _declarations(root)) == (
        f"unresolved: automation.selfskill_audit.extra in {_PACKAGE}/service.py",
    )


def test_a_dropped_declaration_breaks_the_build(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path)
    manifest = root / _MANIFEST
    kept = [line for line in manifest.read_text(encoding="utf-8").splitlines() if "selfskill_audit" not in line]
    _ = manifest.write_text("\n".join(kept) + "\n", encoding="utf-8")
    problems = closure_problems(root, _declarations(root))
    assert problems and all(p.startswith("not shipped: automation/selfskill_audit/") for p in problems)


def init_problems(root: Path, declarations: tuple[Declaration, ...]) -> tuple[str, ...]:
    return tuple(f"shadowing package init: {dest}" for dest in shipped(root, declarations) if dest == _INIT)


def test_the_runtime_automation_directory_never_gets_an_init_file(tmp_path: Path) -> None:
    assert init_problems(_REPO, _declarations(_REPO)) == ()
    root = _copy_repo(tmp_path)
    _ = (root / "automation/__init__.py").write_text("", encoding="utf-8")
    manifest = root / _MANIFEST
    _ = manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f"agent|automation/__init__.py|{_INIT}|v2:file;activation=gateway\n",
        encoding="utf-8",
    )
    assert init_problems(root, _declarations(root)) == (f"shadowing package init: {_INIT}",)


_SSH: Final = """#!/usr/bin/env bash
printf 'ssh %s\\n' "$1" >> "$FAKE_LOG"
[ "$1" = fake-node ] || exit 91
if [ -n "${FAIL_FIRST_AFTER_RUN:-}" ] && [ ! -e "$FAKE_LOG.failed" ]; then
  : > "$FAKE_LOG.failed"; bash -c "$2" > /dev/null; exit 255
fi
exec bash -c "$2"
"""
_SUDO: Final = """#!/usr/bin/env bash
account=""
while [ $# -gt 0 ]; do case "$1" in -n|-H) shift ;; -u) account="$2"; shift 2 ;; *) break ;; esac; done
[ -n "$account" ] && [ "$1" = bash ] && [ "$2" = -lc ] || exit 92
export HOME="$FAKE_NODE/$account"
mkdir -p "$HOME" && cd "$HOME" && exec bash -c "$3"
"""
_TOOL: Final = """#!/usr/bin/env bash
printf '%s {name} %s\\n' "${{HOME##*/}}" "$*" >> "$FAKE_LOG"
[ "$1 $2" = "--user is-active" ] && echo active
exit 0
"""


def _deploy(
    tmp_path: Path, *arguments: str, fail_first: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    shims, node, log = tmp_path / "bin", tmp_path / "node", tmp_path / "calls.log"
    shims.mkdir()
    node.mkdir(exist_ok=True)
    for name, body in (("ssh", _SSH), ("sudo", _SUDO), ("hermes", _TOOL.format(name="hermes")),
                       ("systemctl", _TOOL.format(name="systemctl"))):
        (shims / name).write_text(body, encoding="utf-8")
        (shims / name).chmod(0o755)
    operator = tmp_path / "operator"
    operator.mkdir()
    env = {
        "PATH": f"{shims}:{os.environ['PATH']}", "HOME": str(operator), "DEPLOY_SSH_HOST": "fake-node",
        "DEPLOY_ALLOW_UNPUSHED": "1", "FAKE_NODE": str(node), "FAKE_LOG": str(log),
    }
    if fail_first:
        env["FAIL_FIRST_AFTER_RUN"] = "1"
    result = subprocess.run(("bash", str(_DEPLOY), *arguments), env=env, capture_output=True,
                            text=True, check=False, timeout=120)
    calls = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    return result, calls, node


def test_the_deployer_does_not_restart_without_the_flag(tmp_path: Path) -> None:
    result, calls, node = _deploy(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not [call for call in calls if "restart" in call]
    assert "agent hermes plugins enable 05-skill-generation" in calls
    home = node / "agent"
    for destination, source in shipped(_REPO, _declarations(_REPO)).items():
        assert (home / destination).read_bytes() == (_REPO / source).read_bytes(), destination
    for row in _declarations(_REPO):
        if row.legacy:
            assert (home / row.destination).read_bytes() == (_REPO / row.source).read_bytes()
    assert not (home / _INIT).exists()
    assert not (node / "peer").exists()


def test_restart_flag_restarts_agent_then_peer(tmp_path: Path) -> None:
    result, calls, _ = _deploy(tmp_path, "--restart")
    assert result.returncode == 0, result.stdout + result.stderr
    restarts = [call.split()[0] for call in calls if "systemctl --user restart" in call]
    assert restarts == ["agent", "peer"]
    assert [call for call in calls if "--user is-active" in call] == [
        "agent systemctl --user is-active hermes-gateway.service",
        "peer systemctl --user is-active hermes-gateway.service",
    ]


def test_a_dropped_connection_after_staging_stops_with_the_active_tree_intact(tmp_path: Path) -> None:
    active = tmp_path / "node/agent" / _RUNTIME / "automation/skill_generation"
    active.mkdir(parents=True)
    _ = (active / "core.py").write_text("old\n", encoding="utf-8")
    result, calls, node = _deploy(tmp_path, fail_first=True)
    assert result.returncode == 5, result.stdout + result.stderr
    assert (active / "core.py").read_text(encoding="utf-8") == "old\n"
    assert not list(node.rglob("*.staging.*"))
    assert not [call for call in calls if " hermes " in call or " systemctl " in call]


def test_an_unknown_argument_is_refused_before_any_remote_call(tmp_path: Path) -> None:
    result, calls, _ = _deploy(tmp_path, "--restat")
    assert result.returncode == 2
    assert calls == []
