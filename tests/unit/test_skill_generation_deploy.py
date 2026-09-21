"""Check the generator's transitive imports and execute its archived home runtime.

Start from every generator module, then follow imported modules (including lazy
imports), not unrelated entry points in a dependency package. The audit reporter
is not a generator dependency. Isolated subprocesses prevent the checkout or an
installed automation package from hiding missing deploy artifacts.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_PACKAGE: Final = _REPO / "automation" / "skill_generation"
_DEPLOY: Final = _PACKAGE / "deploy.sh"
_RUNTIME_AUTOMATION: Final = ".hermes/skill-generation/runtime/automation"
_PUSH_TREE: Final = re.compile(
    r"""^push_tree\s+"\$repo_root/automation/([^"]+)"\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def _push_tree_packages() -> frozenset[str]:
    return frozenset(
        match.group(1)
        for match in _PUSH_TREE.finditer(_DEPLOY.read_text(encoding="utf-8"))
        if match.group(2) == _RUNTIME_AUTOMATION
    )


def _imported_names(tree: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return tuple(name for name in names if name.startswith("automation."))


def _import_closure() -> frozenset[Path]:
    pending = list(_PACKAGE.rglob("*.py"))
    visited: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for dotted in _imported_names(tree):
            parts = dotted.split(".")
            # Include package initializers as well as the imported leaf module.
            for depth in range(2, len(parts) + 1):
                base = _REPO.joinpath(*parts[:depth])
                for candidate in (base.with_suffix(".py"), base / "__init__.py"):
                    if candidate.is_file():
                        pending.append(candidate)
    return frozenset(visited)


def test_runtime_push_trees_cover_imported_automation_packages() -> None:
    # Given: deploy.sh declares the runtime archive sources.
    shipped = _push_tree_packages()
    assert "skill_generation" in shipped

    # When: recursively following every generator import down to Python files.
    imported = {path.relative_to(_REPO / "automation").parts[0] for path in _import_closure()}

    # Then: packages AND standalone modules are shipped, including deferred imports.
    assert not (missing := sorted(imported - shipped)), f"Runtime omits automation sources: {missing}"


def test_runtime_sources_are_provenance_checked() -> None:
    # Given: runtime sources are explicit push_tree calls.
    shipped = _push_tree_packages()
    # When: inspecting the existing provenance check's arguments.
    check = _DEPLOY.read_text(encoding="utf-8").split('deploy_provenance_check "$repo_root"', 1)[1].split("||", 1)[0]
    protected = set(re.findall(r'"\$repo_root/automation/([^"]+)"', check))
    # Then: no runtime source bypasses the deployment provenance guard.
    assert shipped <= protected


@pytest.fixture
def runtime_home(tmp_path: Path) -> Path:
    """Use the real archive helper locally; never execute deploy.sh or SSH."""
    destination = tmp_path / _RUNTIME_AUTOMATION
    destination.mkdir(parents=True)
    assembled = subprocess.run(
        (
            "bash", "-c",
            "\n".join((
                'set -euo pipefail; repo="$1"; destination="$2"; shift 2',
                'source "$repo/automation/deploy_provenance.sh"',
                'for entry in "$@"; do',
                'deploy_archive_stream "$repo" "$repo/automation" "$entry" | tar -xzf - -C "$destination"',
                'done',
            )),
            "stage-runtime", str(_REPO), str(destination), *sorted(_push_tree_packages()),
        ),
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=30,
    )
    assert assembled.returncode == 0, assembled.stdout + assembled.stderr
    return tmp_path


def _cli(home: Path, arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, "-I", "-S", str(home / _RUNTIME_AUTOMATION / "skill_generation/cli.py"), *arguments),
        cwd=home,
        env={"HOME": str(home), "AUTOPHAGY_SKILL_LIVE_ROOT": str(home / "live")},
        capture_output=True, text=True, check=False, timeout=10,
    )


@pytest.mark.parametrize("existing", [False, True], ids=["new", "reuse"])
def test_cli_records_precheck_when_only_deployed_sources_exist(runtime_home: Path, existing: bool) -> None:
    # Given: a pristine home with only archives named by deploy.sh, plus two observations.
    text = "compile orchard metrics weekly summaries"
    if existing:
        skill = runtime_home / "live" / "orchard-report"
        skill.mkdir(parents=True)
        _ = (skill / "SKILL.md").write_text(
            f"---\nname: orchard-report\ndescription: {text}\n---\n", encoding="utf-8",
        )
    arguments = ("observe", "--text", text, "--timestamp", "2026-09-14T12:00:00+00:00")
    for _ in range(2):
        seeded = _cli(runtime_home, arguments)
        assert seeded.returncode == 0, seeded.stdout + seeded.stderr

    # When: the real CLI reaches the repetition threshold in isolated Python.
    result = _cli(runtime_home, arguments)

    # Then: precheck is recorded with the expected routing decision, not an ImportError.
    assert result.returncode == 0, result.stdout + result.stderr
    root = runtime_home / ".hermes/skill-generation"
    review = (root / "reviews.jsonl").read_text(encoding="utf-8")
    assert json.loads(review)["verdict"] == ("REUSE-EXISTING" if existing else "NEW")
    assert json.loads(review)["enumerated"] == (["orchard-report"] if existing else [])
    proposal = (root / "proposals.jsonl").read_text(encoding="utf-8")
    assert json.loads(proposal)["status"] == ("REUSE-EXISTING" if existing else "SUGGESTED")


def test_cli_refuses_unknown_command_when_running_from_deployed_runtime(runtime_home: Path) -> None:
    # Given: only deployed sources are available, with no checkout on sys.path.
    # When: the real CLI receives an unsupported command.
    result = _cli(runtime_home, ("unknown-command",))
    # Then: argument parsing refuses the command, rather than failing during imports.
    assert result.returncode == 1
    assert result.stderr.startswith("usage:")


def test_plugin_constructs_service_when_only_deployed_sources_exist(runtime_home: Path) -> None:
    # Given: the plugin entry point comes from the same archive shipped to the gateway.
    plugin = runtime_home / _RUNTIME_AUTOMATION / "skill_generation/plugin/__init__.py"
    # When: the gateway's lazy service factory runs with isolated stdlib-only Python.
    result = subprocess.run(
        (sys.executable, "-I", "-S", "-c",
         "\n".join((
             'import runpy, sys; from pathlib import Path',
             'service = runpy.run_path(sys.argv[1])["_service"]()',
             'assert service.paths.root == Path.home() / ".hermes/skill-generation"',
             'print("SERVICE-READY")',
         )), str(plugin)),
        cwd=runtime_home, env={"HOME": str(runtime_home)},
        capture_output=True, text=True, check=False, timeout=10,
    )
    # Then: the exact factory that failed in pre_gateway_dispatch can initialize.
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "SERVICE-READY"
