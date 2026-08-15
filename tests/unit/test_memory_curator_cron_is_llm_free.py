from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_FORBIDDEN: Final[frozenset[str]] = frozenset(
    {
        "classify",
        "classify_entries",
        "EntryClassifier",
        "LiteLlmClient",
        "llm",
        "classify_model",
        "shadow",
    }
)
_CRON_PATH: Final = (
    Path(__file__).resolve().parents[2]
    / "automation"
    / "memory_curator"
    / "cron"
    / "memory_curator_watch.py"
)


def _references(tree: ast.AST) -> frozenset[str]:
    nodes = tuple(ast.walk(tree))
    names = {node.id for node in nodes if isinstance(node, ast.Name)}
    attributes = {node.attr for node in nodes if isinstance(node, ast.Attribute)}
    import_modules = {
        segment
        for node in nodes
        if isinstance(node, ast.Import)
        for alias in node.names
        for segment in alias.name.split(".")
    }
    import_names = {
        name
        for node in nodes
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        for name in (alias.name, alias.asname)
        if name is not None
    }
    from_modules = {
        segment
        for node in nodes
        if isinstance(node, ast.ImportFrom) and node.module is not None
        for segment in node.module.split(".")
    }
    return frozenset(names | attributes | import_modules | import_names | from_modules)


def _is_run_cycle_call(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Name)
        and call.func.id == "run_cycle"
        or isinstance(call.func, ast.Attribute)
        and call.func.attr == "run_cycle"
    )


def test_cron_does_not_name_llm_classifier_components() -> None:
    # Given: the deployed no-agent cron wrapper AST.
    tree = ast.parse(_CRON_PATH.read_text(encoding="utf-8"))

    # When: its import and attribute names are inspected.
    forbidden_references = _FORBIDDEN & _references(tree)

    # Then: the cron boundary has no classifier or LLM dependency.
    assert forbidden_references == frozenset()


def test_cron_calls_run_cycle_without_classifier_keyword() -> None:
    # Given: the deployed no-agent cron wrapper AST.
    tree = ast.parse(_CRON_PATH.read_text(encoding="utf-8"))
    run_cycle_calls = tuple(
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_run_cycle_call(node)
    )

    # When: the keyword arguments of every run_cycle invocation are inspected.
    classifier_keywords = tuple(
        keyword
        for call in run_cycle_calls
        for keyword in call.keywords
        if keyword.arg == "classifier"
    )

    # Then: cron does not opt into classifier injection.
    assert run_cycle_calls
    assert classifier_keywords == ()
