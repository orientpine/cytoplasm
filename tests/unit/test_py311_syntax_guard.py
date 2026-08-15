"""Deployed-script syntax guard: everything hermes cron may execute must parse on py3.11.

The agent host's hermes runs no-agent cron scripts with its OWN uv-managed
CPython 3.11 (observed: cpython-3.11.15), and watcher wrappers re-invoke
sibling CLIs via ``sys.executable`` — so every ``skills/*/scripts/*.py`` and
``automation/**/*.py`` module (watchers, their imports, and subprocess targets)
must be parseable under the 3.11 grammar. Local tests run on 3.12+, which is
how a PEP 695 ``type`` alias in ``calendar_confirm.py`` shipped and broke the
``calendar-confirm-watch`` cron with a SyntaxError. This guard rejects any
3.12+-only syntax at test time.

``ast.parse(feature_version=(3, 11))`` alone is NOT sufficient: ``feature_version``
gates *parser* features only, while PEP 701 relaxed f-strings in the 3.12
*tokenizer*. A nested same-quote f-string like ``f"{row["k"]}"`` therefore parses
clean on 3.12 even with ``feature_version=(3, 11)``, yet still dies on real 3.11
with ``SyntaxError: f-string: unmatched '['``. That gap let ``triage_gate.py``
ship an unimportable f-string, which broke ``triage_cli`` at import and silently
stopped the mail approval watcher (repair ticket t_90a2e810). The
``py312_only_fstrings`` scan below closes it by inspecting replacement fields
directly, so the regression is caught on a 3.12 dev machine with no 3.11 present.
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

# The exact shape that shipped in triage_gate.execute_draft and broke prod (t_90a2e810).
_HISTORIC_BUG = '''\
raise GateError(
    f"(error_code={error["error_code"]} stage={error["stage"]} "
    f"retryable={str(error["retryable"]).lower()} consecutive={failures})",
    exit_code,
)
'''
_REPAIRED = '''\
raise GateError(
    f"(error_code={error['error_code']} stage={error['stage']} "
    f"retryable={str(error['retryable']).lower()} consecutive={failures})",
    exit_code,
)
'''
_BACKSLASH_FIELD = r'''summary = f"{'\n'.join(rows)}"
'''


def _deployed_sources() -> list[Path]:
    files = sorted(_REPO.glob("skills/*/scripts/*.py"))
    files += sorted(
        path for path in _REPO.glob("automation/**/*.py") if "__pycache__" not in path.parts
    )
    assert files, "glob found no deployed scripts — repo layout changed?"
    return files


def _fstring_quote(start_token: str) -> str:
    """Strip an FSTRING_START prefix (``f``/``rf``/``F``…) down to its quote delimiter."""
    return start_token.lstrip("fFrRbBuU")


def py312_only_fstrings(source: str, filename: str) -> list[str]:
    """Report f-strings whose ``{...}`` fields only tokenize under PEP 701 (3.12+).

    Illegal before 3.12 inside a replacement field: reusing a quote that delimits
    any enclosing f-string, a backslash, or a comment. Each literal of an
    implicitly concatenated f-string carries its own delimiter, so the stack
    tracks the enclosing literal rather than the first one of the concatenation.
    """
    start = getattr(tokenize, "FSTRING_START", None)
    if start is None:  # running on <=3.11, where the interpreter rejects these shapes itself
        return []
    failures: list[str] = []
    enclosing: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, tokenize.TokenError):
        return failures  # a genuine syntax error belongs to the grammar guard above
    for token in tokens:
        if token.type == start:
            enclosing.append(_fstring_quote(token.string))
            continue
        if token.type == tokenize.FSTRING_END:
            if enclosing:
                _ = enclosing.pop()
            continue
        if not enclosing or token.type == tokenize.FSTRING_MIDDLE:
            continue
        line = token.start[0]
        for quote in enclosing:
            if quote in token.string:
                failures.append(f"{filename}:{line}: {quote} reused inside f-string field")
                break
        if "\\" in token.string:
            failures.append(f"{filename}:{line}: backslash inside f-string field")
        if token.type == tokenize.COMMENT:
            failures.append(f"{filename}:{line}: comment inside f-string field")
    return failures


def test_deployed_scripts_parse_under_py311_grammar() -> None:
    failures: list[str] = []
    for path in _deployed_sources():
        try:
            _ = ast.parse(
                path.read_text(encoding="utf-8"), str(path), feature_version=(3, 11)
            )
        except SyntaxError as error:
            failures.append(f"{path.relative_to(_REPO)}:{error.lineno}: {error.msg}")
    assert not failures, "py3.12+-only syntax in hermes-executed scripts:\n" + "\n".join(failures)


def test_repair_report_consumer_does_not_import_py312_typing_names() -> None:
    unavailable = {"override"}
    failures: list[str] = []
    path = _REPO / "automation/repair/repair_report_consumer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 11))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "typing":
            continue
        imported = unavailable.intersection(alias.name for alias in node.names)
        if imported:
            failures.append(f"{path.relative_to(_REPO)}:{node.lineno}: {', '.join(sorted(imported))}")
    assert not failures, "py3.12+ typing imports in hermes-executed modules:\n" + "\n".join(failures)


def test_feature_version_alone_misses_pep701_fstrings() -> None:
    """Lock in WHY the extra scan exists: feature_version accepts the prod-breaking shape."""
    assert ast.parse(_HISTORIC_BUG, "<historic>", feature_version=(3, 11)) is not None


def test_scan_catches_historic_nested_quote_regression() -> None:
    assert py312_only_fstrings(_HISTORIC_BUG, "<historic>")


def test_scan_accepts_repaired_inner_single_quote_form() -> None:
    assert py312_only_fstrings(_REPAIRED, "<repaired>") == []


def test_scan_catches_backslash_in_replacement_field() -> None:
    assert py312_only_fstrings(_BACKSLASH_FIELD, "<backslash>")


def test_deployed_scripts_have_no_py312_only_fstrings() -> None:
    failures = [
        problem
        for path in _deployed_sources()
        for problem in py312_only_fstrings(
            path.read_text(encoding="utf-8"), str(path.relative_to(_REPO))
        )
    ]
    assert not failures, "PEP 701 (3.12-only) f-strings in hermes-executed scripts:\n" + "\n".join(
        failures
    )
