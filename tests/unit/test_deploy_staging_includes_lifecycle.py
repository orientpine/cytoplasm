"""The staged deploy gate must ship every automation module it imports.

Bootstrapping deadlock this locks: automation/deploy-skill.sh stages
automation/skill_gate.py onto the node together with a hand-written list of
helper modules. When skill_gate.py grew an import of
automation.interop.approval_lifecycle (which imports approval_lease) without
those two modules joining the staging list, the staged gate raised ImportError
at runtime — so *every* skill deploy in the repo failed closed, including the
deploy that would have shipped the fix.

The staging list is the only place in the repo that enumerates individual
automation/** modules (deploy-skill.sh tars whole skill directories otherwise),
so it is the only place this class of deadlock can be introduced.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "automation" / "deploy-skill.sh"

_HELPER_ARRAY = re.compile(
    r"^(?:GATE_HELPERS|GATE_INTEROP_HELPERS)=\(([^)]*)\)$",
    re.MULTILINE,
)


def _script() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _code(script: str) -> str:
    """Script with comment lines stripped, so no assertion can be met by a comment."""
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


def _staged_modules(script: str) -> tuple[str, ...]:
    """Every automation/** module the gate stages, derived from the script itself."""
    modules = ["skill_gate.py"]
    for match in _HELPER_ARRAY.finditer(_code(script)):
        modules.extend(shlex.split(match.group(1)))
    return tuple(modules)


def test_gate_staging_when_gate_imports_lifecycle_then_stages_lease_and_lifecycle() -> None:
    # Given
    script = _script()

    # When
    modules = _staged_modules(script)

    # Then
    assert "interop/approval_lease.py" in modules
    assert "interop/approval_lifecycle.py" in modules


def test_gate_staging_when_module_is_under_interop_then_copies_into_interop_runtime_subdir() -> None:
    # Given
    code = _code(_script())

    # When: EVERY interop staging loop, not just the first. The sandbox stages the
    # same list onto the peer so the scenario can resolve the policy, and both loops
    # must lay the modules out identically or the two nodes drift apart.
    marker = 'for helper in "${GATE_INTEROP_HELPERS[@]}"; do'
    bodies = []
    cursor = code.find(marker)
    while cursor != -1:
        bodies.append(code[cursor : code.index("\ndone", cursor)])
        cursor = code.find(marker, cursor + 1)

    # Then
    assert bodies, "no interop staging loop found"
    for body in bodies:
        assert 'mkdir -p \\"\\$HOME/.hermes/interop_runtime/automation/interop\\"' in body
        assert 'cat > \\"\\$HOME/.hermes/interop_runtime/automation/$helper\\"' in body
        assert '< "$REPO_ROOT/automation/$helper"' in body
        assert "umask 077" in body
    accounts = {account for body in bodies for account in ("$NODE_AGENT_ACCOUNT", "$NODE_PEER_ACCOUNT") if f'run_as "{account}"' in body}
    assert accounts == {"$NODE_AGENT_ACCOUNT"}, (
        f"interop modules staged for {accounts or 'nobody'}; the sandbox peer reads the ops "
        "checkout via AUTOPHAGY_REPO_ROOT instead of holding a second copy that can drift"
    )


def test_gate_staging_when_module_is_staged_then_hardened_to_owner_only() -> None:
    # Given
    code = _code(_script())

    # When — the hardening that belongs to the gate staging block, not the
    # earlier review-tool chmod.
    builder_idx = code.index("GATE_STAGED_PATHS=")
    builder = code[builder_idx : code.index("\ndone", builder_idx)]
    chmod_idx = code.index('run_as "$NODE_AGENT_ACCOUNT" "chmod 600', builder_idx)
    chmod_line = code[chmod_idx : code.index("\n", chmod_idx)]

    # Then — hardening is derived from the staging arrays, so it cannot drift
    # out of sync when a module is added to the list.
    assert "$GATE_STAGED_PATHS" in chmod_line
    assert '"${GATE_HELPERS[@]}" "${GATE_INTEROP_HELPERS[@]}"' in builder
    assert "GATE_STAGED_PATHS+=" in builder


def test_gate_staging_when_list_names_a_module_then_that_module_exists_in_checkout() -> None:
    # Given
    modules = _staged_modules(_script())

    # When / Then
    assert len(modules) >= 5
    for module in modules:
        assert (ROOT / "automation" / module).is_file(), f"staged module missing: automation/{module}"


def test_gate_staging_when_a_module_is_missing_then_preflight_dies_before_any_copy() -> None:
    # Given
    code = _code(_script())

    # When
    preflight_idx = code.index(
        'for src in skill_gate.py "${GATE_HELPERS[@]}" "${GATE_INTEROP_HELPERS[@]}"; do'
    )
    preflight = code[preflight_idx : code.index("\ndone", preflight_idx)]

    # Then — the guard checks the checkout paths and fails closed by name...
    assert '[[ -f "$REPO_ROOT/automation/$src" ]]' in preflight
    assert "STAGE-BLOCK: gate module missing from checkout: automation/$src" in preflight
    # ...before the first byte of any module is copied to the node.
    assert preflight_idx < code.index('cat > \\"\\$HOME/.hermes/skill-gate/skill_gate.py\\"')
    assert preflight_idx < code.index('for helper in "${GATE_HELPERS[@]}"; do')
    assert preflight_idx < code.index('for helper in "${GATE_INTEROP_HELPERS[@]}"; do')


def test_deploy_script_when_edited_then_still_parses_as_bash() -> None:
    # Given / When
    result = subprocess.run(  # noqa: S603
        ["/usr/bin/env", "bash", "-n", str(DEPLOY)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    # Then
    assert result.returncode == 0, result.stderr
