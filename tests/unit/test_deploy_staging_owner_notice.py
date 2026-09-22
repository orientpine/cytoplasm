"""Owner-notice runtime staging stays separate from the attested skill and gate."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest
from tests.unit import test_deploy_staging_is_derived_from_imports as gate

ROOT: Final = Path(__file__).resolve().parents[2]
DEPLOY: Final = ROOT / "automation/deploy-skill.sh"
ENTRIES: Final = frozenset({
    "automation/owner_notice.py",
    "automation/interop/origin_notice.py",
    "automation/interop/discord_transport.py",
})


def facade_closure() -> frozenset[str]:
    """Follow the real facade imports, including nested and relative imports."""
    seen: set[str] = set()
    queue = list(ENTRIES)
    while queue:
        current = queue.pop()
        if current not in seen:
            seen.add(current)
            queue.extend(gate._automation_imports(ROOT / current))
    return frozenset(seen)


def test_gate_closure_excludes_notice_delivery_when_facades_are_available() -> None:
    # Given: notice delivery modules are available in the checkout. `interop/chunker.py`
    # is NOT one of them any more: it is a pure text splitter with no transport, and the
    # staged gate imports it on purpose (release_spec replays the detail messages of a
    # stored card), so it is staged as a gate helper. What must stay out is delivery —
    # the facades that can actually reach Discord.
    forbidden = ENTRIES
    # When: the gate import closure is followed independently of the notice facade.
    imported = gate._required()
    # Then: the staged gate does not acquire any delivery dependency.
    assert not imported & forbidden


def test_gate_staged_paths_only_harden_permissions_when_peer_attests() -> None:
    # Given: the deploy script contains both permission and attestation wiring.
    script = DEPLOY.read_text(encoding="utf-8")
    # When: all reads of the staged-path variable and the peer input are inspected.
    uses = re.findall(r"^.*\$GATE_STAGED_PATHS.*$", script, re.MULTILINE)
    peer = script[script.index("peer_attest() {"):script.index('\nPEER_ATTEST_BLOB=""')]
    # Then: staged paths feed chmod alone; peer attests the skill directory.
    assert uses == ['run_as "$NODE_AGENT_ACCOUNT" "chmod 600 $GATE_STAGED_PATHS"']
    assert '--staged-dir \\"\\$HOME/.hermes/skills/$name\\"' in peer
    assert "GATE_" not in peer


def facade_staged(script: str) -> frozenset[str]:
    """Only the independent notice list counts, not any gate payload overlap."""
    arrays = re.finditer(r"^OWNER_NOTICE_HELPERS=\(([^)]*)\)$", script, re.MULTILINE)
    return frozenset(f"automation/{name}" for array in arrays for name in array.group(1).split())


def test_facade_closure_is_staged_when_skills_import_notice_runtime() -> None:
    # Given: skills resolve these three entry points from interop_runtime.
    script = DEPLOY.read_text(encoding="utf-8")
    # When: their actual transitive imports are compared to the independent list.
    missing = sorted(facade_closure() - facade_staged(script))
    # Then: missing modules are named, even if they happen to be gate helpers too.
    assert not missing, f"OWNER_NOTICE_HELPERS missing import closure: {missing}"


def test_facade_loop_is_agent_only_when_notice_helpers_are_staged() -> None:
    # Given: only executable lines can satisfy this staging contract.
    script = "\n".join(line for line in DEPLOY.read_text(encoding="utf-8").splitlines()
                       if not line.lstrip().startswith("#"))
    # When: every loop consuming the independent notice list is inspected.
    loops = list(re.finditer(r'for helper in "\$\{OWNER_NOTICE_HELPERS\[@\]\}"; do\n(.*?)\ndone',
                             script, re.DOTALL))
    # Then: notice copies target agent runtime only, with owner-private writes.
    assert len(loops) == 1, "OWNER_NOTICE_HELPERS needs one agent-only copy loop"
    body = loops[0].group(1)
    assert 'run_as "$NODE_AGENT_ACCOUNT"' in body
    assert "$NODE_PEER_ACCOUNT" not in body
    assert "umask 077" in body
    assert 'cat > \\"\\$HOME/.hermes/interop_runtime/automation/$helper\\"' in body
    assert '< "$REPO_ROOT/automation/$helper"' in body
    assert "chmod 600" in body


@pytest.mark.parametrize("stale", [False, True], ids=["fresh", "stale"])
def test_facade_runtime_refreshes_when_local_staging_runs(tmp_path: Path, stale: bool) -> None:
    # Given: a private fake account, optionally containing stale permissive files.
    script = DEPLOY.read_text(encoding="utf-8")
    arrays = [match.group() for match in re.finditer(r"^OWNER_NOTICE_HELPERS=\([^)]*\)$", script, re.MULTILINE)]
    loops = [match.group() for match in re.finditer(
        r'for helper in "\$\{OWNER_NOTICE_HELPERS\[@\]\}"; do\n.*?\ndone', script, re.DOTALL)]
    assert len(arrays) == len(loops) == 1, "notice staging block missing"
    runtime = tmp_path / ".hermes/interop_runtime"
    if stale:
        # Bootstrap owns directories; only file contents and modes are stale here.
        for directory in (runtime, runtime / "automation", runtime / "automation/interop"):
            directory.mkdir(mode=0o700, parents=True)
        for name in sorted(facade_closure()):
            path = runtime / name
            _ = path.write_text("stale", encoding="utf-8")
            path.chmod(0o644)
    # run_as replaces only account switching; the actual shell copy commands run.
    harness = '\n'.join((
        'set -euo pipefail', 'REPO_ROOT="$1"', 'export HOME="$2"',
        'NODE_AGENT_ACCOUNT=fixture-agent',
        'run_as() { [[ "$1" == "$NODE_AGENT_ACCOUNT" ]] || return 91; bash -eu -c "$2"; }',
        arrays[0], loops[0],
    ))
    # When: the real staging block runs locally, without any deployment entry point.
    result = subprocess.run(["bash", "-c", harness, "staging-test", str(ROOT), str(tmp_path)],
                            capture_output=True, text=True, timeout=30, check=False)
    # Then: every required module is current, owner-only and importable in isolation.
    assert result.returncode == 0, result.stderr
    for name in facade_closure():
        path = runtime / name
        assert path.read_bytes() == (ROOT / name).read_bytes(), name
        assert path.stat().st_mode & 0o777 == 0o600, name
    for directory in (runtime, runtime / "automation", runtime / "automation/interop"):
        assert directory.stat().st_mode & 0o777 == 0o700
    smoke = subprocess.run([
        sys.executable, "-I", "-c",
        "\n".join((
            "import importlib, sys; sys.path.insert(0, sys.argv[1])",
            "[importlib.import_module(name) for name in sys.argv[2:]]",
            "from automation import owner_notice; from automation.interop import origin_notice",
            "assert owner_notice.ACCEPTS_OWNER_MESSAGE and origin_notice.ACCEPTS_OWNER_MESSAGE",
        )),
        str(runtime), *(name.removesuffix(".py").replace("/", ".") for name in sorted(facade_closure())),
    ], cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False)
    assert smoke.returncode == 0, smoke.stderr


def test_facade_preflight_refuses_when_a_source_module_is_missing(tmp_path: Path) -> None:
    # Given: the actual preflight against a checkout missing one declared module.
    script = DEPLOY.read_text(encoding="utf-8")
    arrays = [match.group() for match in re.finditer(r"^OWNER_NOTICE_HELPERS=\([^)]*\)$", script, re.MULTILINE)]
    loops = [match.group() for match in re.finditer(
        r'for src in "\$\{OWNER_NOTICE_HELPERS\[@\]\}"; do\n.*?\ndone', script, re.DOTALL)]
    assert len(arrays) == len(loops) == 1, "notice source preflight missing"
    missing = "automation/interop/chunker.py"
    for name in facade_staged(script) - {missing}:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes((ROOT / name).read_bytes())
    harness = '\n'.join(('set -euo pipefail', 'REPO_ROOT="$1"',
                         'die() { printf "%s\\n" "$1" >&2; exit 1; }', arrays[0], loops[0]))
    # When: the source preflight runs, without invoking any node operation.
    result = subprocess.run(["bash", "-c", harness, "preflight-test", str(tmp_path)],
                            capture_output=True, text=True, timeout=30, check=False)
    # Then: the missing module refuses staging before any runtime copy is possible.
    assert result.returncode == 1
    assert "STAGE-BLOCK" in result.stderr and missing in result.stderr
    assert script.index(loops[0]) < script.index('for helper in "${GATE_HELPERS[@]}"; do')
