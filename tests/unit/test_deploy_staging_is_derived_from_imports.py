"""The staging list must cover every automation module the staged gate imports.

``automation/deploy-skill.sh`` stages ``automation/skill_gate.py`` onto the node
with a HAND-WRITTEN list of helper modules. That list is the only place in the
repo enumerating individual ``automation/**`` modules, and it rots silently: a
new import lands, nobody edits the array, and the staged gate raises ImportError
at runtime — so EVERY skill deploy in the repo fails closed, including the deploy
that would ship the fix. It has already happened twice (approval_lifecycle +
approval_lease, then skill_gate_surface + approval_surface + approval_directory).

``test_deploy_staging_includes_lifecycle.py`` pins the list's SHAPE — that it is
copied, hardened and preflighted. This file pins its CONTENTS, and derives them
from the imports themselves rather than from a second hand-written list, so the
next module to join the chain fails this test instead of production.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[2]
DEPLOY: Final = ROOT / "automation" / "deploy-skill.sh"
ENTRY: Final = "automation/skill_gate.py"

_HELPER_ARRAY: Final = re.compile(
    r"^(?:GATE_HELPERS|GATE_INTEROP_HELPERS)=\(([^)]*)\)$",
    re.MULTILINE,
)

# Imports the staged gate is designed to survive WITHOUT. Each entry must name a
# real tolerated-ImportError site, or `test_optional_imports_are_actually_tolerated`
# fails — an exemption that stops being true is worse than no exemption.
_OPTIONAL: Final[dict[str, str]] = {
    "automation/skill_gate_publish.py": (
        "publish subcommands only resolve on the workstation; skill_gate.py wraps the "
        "import in try/except ModuleNotFoundError so the staged agent gate omits it (302f7d7)"
    ),
}


def _staged() -> frozenset[str]:
    """Every ``automation/<path>`` the deploy script copies onto the node."""
    script = DEPLOY.read_text(encoding="utf-8")
    modules = {ENTRY}
    for array in _HELPER_ARRAY.findall(script):
        modules.update(f"automation/{name}" for name in array.split())
    return frozenset(modules)


def _automation_imports(path: Path) -> set[str]:
    """``automation/...`` module paths imported by this file, at any nesting depth."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for dotted in names:
            if dotted == "automation" or not dotted.startswith("automation."):
                continue
            candidate = ROOT / (dotted.replace(".", "/") + ".py")
            if candidate.is_file():
                found.add(str(candidate.relative_to(ROOT)))
    return found


def _required() -> frozenset[str]:
    """Transitive closure of automation imports reachable from the staged modules."""
    seen: set[str] = set()
    queue = list(_staged())
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        path = ROOT / current
        if path.is_file():
            queue.extend(_automation_imports(path))
    return frozenset(seen)


def test_staging_covers_every_automation_import_of_the_staged_chain() -> None:
    # Given: the modules the deploy script actually copies onto the node.
    staged = _staged()

    # When: every automation import reachable from them is followed.
    missing = sorted(_required() - staged - frozenset(_OPTIONAL))

    # Then: nothing the staged gate imports is left behind on the workstation.
    assert not missing, (
        "deploy-skill.sh stages a gate that imports modules it does not copy — "
        "the staged gate would raise ImportError and EVERY skill deploy would fail "
        "closed, including the deploy shipping the fix. Add to GATE_HELPERS / "
        f"GATE_INTEROP_HELPERS: {missing}"
    )


def test_optional_imports_are_actually_tolerated() -> None:
    # Given: each module exempted as an optional import.
    for module, reason in _OPTIONAL.items():
        assert (ROOT / module).is_file(), f"stale exemption, module gone: {module} ({reason})"
        dotted = module.removesuffix(".py").replace("/", ".")

        # When: the importers in the staged chain are inspected.
        guarded = [
            path
            for path in _staged()
            if (ROOT / path).is_file() and dotted in (ROOT / path).read_text(encoding="utf-8")
        ]
        assert guarded, f"nothing in the staged chain imports {module}; drop the exemption"

        # Then: at least one importer wraps it so a missing module cannot break the gate.
        tolerated = any(
            any(
                isinstance(handler.type, ast.Name)
                and handler.type.id in {"ModuleNotFoundError", "ImportError"}
                for node in ast.walk(
                    ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
                )
                if isinstance(node, ast.Try)
                and dotted
                in ast.unparse(ast.Module(body=node.body, type_ignores=[]))
                for handler in node.handlers
            )
            for path in guarded
        )
        assert tolerated, (
            f"{module} is exempted as optional but no staged importer guards it with "
            "try/except ImportError — it is a hard dependency and must be staged"
        )


def test_every_staged_module_exists_in_the_checkout() -> None:
    # Given/When: the staging list is read.
    absent = sorted(name for name in _staged() if not (ROOT / name).is_file())

    # Then: a renamed or deleted module is caught here, not by a half-staged node.
    assert not absent, f"staging list names modules that do not exist: {absent}"


def test_deploy_runs_import_coverage_check_before_copying_gate_modules() -> None:
    # Given: the deploy script owns the staging arrays and the copy loops.
    script = DEPLOY.read_text(encoding="utf-8")
    checker = script.index("validate_gate_staging_imports() {")
    invocation = script.index("\nvalidate_gate_staging_imports\n")
    first_copy = script.index('for helper in "${GATE_HELPERS[@]}"; do')

    # When / Then: AST-derived coverage is enforced before any gate module is copied.
    assert checker < invocation < first_copy
    assert "ast.parse" in script[checker:invocation]
    assert "STAGE-BLOCK: imported gate module is not staged" in script[checker:invocation]
