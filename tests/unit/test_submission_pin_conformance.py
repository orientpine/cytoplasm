"""Conformance pin for submission tarball authenticity (F5 residual B).

``validate_submission_artifact`` takes ``expected_tarball_sha256`` and defaults
it to ``None``.  With the default it never compares the bytes to anything
external: it only checks that the tarball agrees with the manifest that arrived
beside it, so a submitter who replaces both files consistently passes.

That default is legitimate at exactly one place - the operator-supplied intake
in ``publish_command._publish_input``, which has nothing to pin to yet because
the pin lives in the group admin's approved Discord message.  Every other caller
already knows which bytes it means and must say so.

These checks make that boundary mechanical rather than remembered: a new caller
is a conformance failure until it is classified, an exemption is a failure once
it stops being unpinned, and the one exempt result may not flow anywhere except
into the approval binding that authenticates it.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Final

from automation.managed_skills.submission_artifact import (
    extract_submission,
    validate_submission_artifact,
)

_REPO: Final = Path(__file__).resolve().parents[2]
_TARGET: Final = "validate_submission_artifact"
_PIN: Final = "expected_tarball_sha256"
_ROOT: Final = _REPO / "automation"

#: Sites that must pass an explicit pin, with what each one is pinning against.
_PINNED: Final[dict[str, str]] = {
    "managed_skills/submission_artifact.py::package_personal_skill": (
        "pins the digest it just computed over the bytes it just wrote"
    ),
    "managed_skills/submission_artifact.py::extract_submission": (
        "re-validates against the artifact's own recorded digest before yielding"
    ),
    "managed_skills/submission_approval.py::post": (
        "re-reads the artifacts at post time and pins them to the record being posted"
    ),
    "managed_skills/submission_approval.py::request_submission_approval": (
        "binds the review request's action hash to specific bytes"
    ),
    "managed_skills/submission_source.py::open_approved_submission": (
        "re-checks the bytes the admin approved before they become a publish input"
    ),
}

#: The single site allowed to rely on the ``None`` default, and why.
_EXEMPT: Final[dict[str, str]] = {
    "managed_skills/publish_command.py::_publish_input": (
        "operator-supplied intake: nothing external to pin to yet. The pin is the "
        "group admin's approved Discord message, which open_approved_submission "
        "fetches and binds immediately afterwards. Confined by "
        "test_the_unpinned_intake_flows_only_into_the_approval_binding."
    ),
}

#: Where the exempt result is allowed to go, and the keyword it must arrive as.
_EXEMPT_SINK: Final = ("ApprovedSubmissionConfig", "artifact")


def _modules() -> tuple[Path, ...]:
    return tuple(sorted(path for path in _ROOT.rglob("*.py") if path.is_file()))


def _surface(path: Path, function: str) -> str:
    return f"{path.relative_to(_ROOT).as_posix()}::{function}"


def _call_sites() -> dict[str, list[ast.Call]]:
    """Map ``module::enclosing function`` to every validate call inside it."""
    sites: dict[str, list[ast.Call]] = {}
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for parent in ast.walk(tree):
            if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [
                node
                for node in ast.walk(parent)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == _TARGET
            ]
            if calls:
                sites.setdefault(_surface(path, parent.name), []).extend(calls)
    return sites


def _pins(call: ast.Call) -> bool:
    return any(keyword.arg == _PIN for keyword in call.keywords)


def _function(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is no longer defined in {path}")


def test_the_unpinned_default_still_exists_and_is_still_unpinned() -> None:
    # Given: the two signatures this whole guard is written around.
    validate = inspect.signature(validate_submission_artifact)
    extract = inspect.signature(extract_submission)

    # Then: the risky default is still the default (if this ever changes to a
    # required argument, this module has done its job and can be retired) ...
    assert validate.parameters[_PIN].default is None
    # ... and extraction still re-validates unless a caller opts out.
    assert extract.parameters["revalidate"].default is True


def test_every_validate_call_site_is_classified() -> None:
    # Given: every call to validate_submission_artifact under automation/.
    observed = frozenset(_call_sites())
    declared = frozenset(_PINNED) | frozenset(_EXEMPT)

    # Then: a new caller cannot appear without a recorded pin decision, and a
    # removed caller cannot linger as a stale classification.
    unclassified = sorted(observed - declared)
    stale = sorted(declared - observed)
    assert not unclassified, (
        f"new {_TARGET} callers must declare whether they pin: {unclassified}"
    )
    assert not stale, f"classified {_TARGET} callers no longer exist: {stale}"


def test_classified_publish_path_sites_pass_an_explicit_pin() -> None:
    # Given: every site declared as pinning.
    sites = _call_sites()

    # Then: each really passes expected_tarball_sha256 rather than defaulting.
    unpinned = sorted(
        surface
        for surface in _PINNED
        if not all(_pins(call) for call in sites.get(surface, ()))
    )
    assert not unpinned, (
        "these sites are declared as pinning but rely on the None default - an "
        f"unpinned artifact is not authenticated: {unpinned}"
    )


def test_exemptions_are_removed_once_a_site_starts_pinning() -> None:
    # Given: every site excused from pinning.
    sites = _call_sites()

    # Then: none of them quietly gained a pin while keeping its excuse.
    pinning = sorted(
        surface for surface in _EXEMPT if any(_pins(call) for call in sites.get(surface, ()))
    )
    assert not pinning, f"stale pin exemptions - these sites now pin: {pinning}"


def test_the_unpinned_intake_flows_only_into_the_approval_binding() -> None:
    # Given: the one function allowed to hold an unauthenticated artifact.
    module, name = next(iter(_EXEMPT)).split("::")
    function = _function(_ROOT / module, name)
    bound = {
        target.id
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == _TARGET
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert bound, f"{name} no longer binds the unpinned {_TARGET} result to a name"

    # When: every other use of that name is collected.
    sink_type, sink_keyword = _EXEMPT_SINK
    escapes: list[str] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == _TARGET:
            continue
        for keyword in node.keywords:
            if not isinstance(keyword.value, ast.Name) or keyword.value.id not in bound:
                continue
            accepted = (
                isinstance(node.func, ast.Name)
                and node.func.id == sink_type
                and keyword.arg == sink_keyword
            )
            if not accepted:
                escapes.append(f"{ast.unparse(node.func)}(..., {keyword.arg}=...)")
        escapes.extend(
            f"{ast.unparse(node.func)}(positional)"
            for argument in node.args
            if isinstance(argument, ast.Name) and argument.id in bound
        )

    # Then: it reaches nothing but the admin-approval binding that authenticates it.
    assert not escapes, (
        f"the unpinned artifact in {module}::{name} must reach only "
        f"{sink_type}({sink_keyword}=...) - it escaped to: {sorted(set(escapes))}"
    )
