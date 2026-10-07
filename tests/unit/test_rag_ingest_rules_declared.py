"""RCB todo 26: the sensitivity-rules copy is declared and release-converged."""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from automation.deploy_declarations import all_declarations
from tests.unit.cron_fixture import declared_cron, listing_only_hermes

_REPO = Path(__file__).resolve().parents[2]
_SOURCE = "configs/sensitivity-rules.yaml"
_DESTINATION = ".hermes/rag-ingest/sensitivity-rules.yaml"


def _calls(path: Path, attribute: str, first_argument: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == first_argument
    ]


def _string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _knowledge_default() -> str:
    path = _REPO / "automation/knowledge/adapters/wiki.py"
    found = [
        literal
        for call in _calls(path, "get", "KNOWLEDGE_SENSITIVITY_RULES")
        if len(call.args) >= 2 and (literal := _string(call.args[1])) is not None
    ]
    assert len(found) == 1, f"KNOWLEDGE_SENSITIVITY_RULES default missing in {path}: {found}"
    return found[0]


def _curate_default() -> str:
    path = _REPO / "automation/wiki_curate/cli.py"
    found: list[str] = []
    for call in _calls(path, "add_argument", "--sensitivity-rules"):
        for keyword in call.keywords:
            value = keyword.value
            if (
                keyword.arg == "default"
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "Path"
                and len(value.args) == 1
                and (literal := _string(value.args[0])) is not None
            ):
                found.append(literal)
    assert len(found) == 1, f"--sensitivity-rules default=Path(...) missing in {path}: {found}"
    return found[0]


def test_the_rules_copy_is_declared_and_pushed() -> None:
    declarations = [item for item in all_declarations(_REPO) if item.destination == _DESTINATION]
    assert len(declarations) == 1, declarations
    row = declarations[0]
    assert (row.account, row.source, row.kind, row.policy) == (
        "agent",
        _SOURCE,
        "file",
        "required",
    )
    deployer = (_REPO / "automation/rag_ingest/deploy.sh").read_text(encoding="utf-8")
    assert f'push_file "$repo_root/{_SOURCE}" \'{_DESTINATION}\'' in deployer


def test_the_declared_path_is_the_one_the_consumers_default_to(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Both consumers wrap the literal in Path(...).expanduser(); expand it the same way.
    monkeypatch.setenv("HOME", str(tmp_path))
    declared = tmp_path / _DESTINATION
    defaults = {
        "automation/knowledge/adapters/wiki.py": _knowledge_default(),
        "automation/wiki_curate/cli.py": _curate_default(),
    }
    for consumer, literal in defaults.items():
        assert Path(literal).expanduser() == declared, (consumer, literal)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True,
        timeout=60,
    ).stdout.strip()


def test_the_rules_file_is_under_the_provenance_guard(tmp_path: Path) -> None:
    """Run the real deployer in a repo copy whose rules file is dirty: it must block."""
    copy = tmp_path / "repo"
    store = _git(_REPO, "rev-parse", "--path-format=absolute", "--git-common-dir")
    _git(_REPO, "clone", "--quiet", "--shared", "--no-checkout", store, str(copy))
    _git(copy, "checkout", "--quiet", "--detach", _git(_REPO, "rev-parse", "HEAD"))
    # Bring in the working-tree deployer and everything it sources (deploy_tree.sh,
    # deploy_cron.sh, ...), then make them the reference. HEAD may predate those helpers:
    # the public export runs this suite in a clone whose HEAD is the previous snapshot.
    for relative in ("automation", _SOURCE):
        source, target = _REPO / relative, copy / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target)
    _git(copy, "add", "--all", "--", "automation", "configs")
    reference = _git(
        copy, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit-tree", _git(copy, "write-tree"), "-p", "HEAD", "-m", "reference",
    )
    # No fetchable origin: the guard's fetch fails softly and origin/main stays this ref.
    _git(copy, "remote", "remove", "origin")
    _git(copy, "update-ref", "refs/remotes/origin/main", reference)
    with (copy / _SOURCE).open("a", encoding="utf-8") as rules:
        rules.write("# local edit absent from origin/main\n")

    bin_dir, marker = tmp_path / "bin", tmp_path / "ssh-called"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(f"#!/bin/bash\ntouch {marker}\nexit 1\n", encoding="utf-8")
    ssh.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        ("bash", str(copy / "automation/rag_ingest/deploy.sh")),
        cwd=copy,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home),
             "DEPLOY_SSH_HOST": "fake-node"},
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert f"DEPLOY-BLOCK: {_SOURCE} differs from origin/main" in result.stderr
    assert not marker.exists()


def _deployer_env(tmp_path: Path, *, fail_rules: bool = False) -> tuple[Path, dict[str, str]]:
    home, bin_dir = tmp_path / "home", tmp_path / "bin"
    home.mkdir(parents=True)
    bin_dir.mkdir()
    shims = {
        "ssh": (
            "#!/bin/bash\nshift\n"
            'if [[ "${FAIL_RULES:-0}" == 1 && "$1" == *".hermes/rag-ingest/sensitivity-rules.yaml"* ]]; then exit 17; fi\n'
            'exec bash -c "$1"\n'
        ),
        "sudo": (
            "#!/bin/bash\nwhile [[ \"$1\" != bash ]]; do shift; done\n"
            "shift; shift\nexec bash -c \"$1\"\n"
        ),
        "hermes": listing_only_hermes(declared_cron("automation/rag_ingest")),
    }
    for name, content in shims.items():
        shim = bin_dir / name
        shim.write_text(content, encoding="utf-8")
        shim.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home),
        "DEPLOY_SSH_HOST": "fake-node",
        "DEPLOY_ALLOW_UNPUSHED": "1",
        "DEPLOY_TREE_LOCK_WAIT": "1",
        "FAIL_RULES": "1" if fail_rules else "0",
    }
    return home, env


def test_the_real_deployer_replaces_stale_rules_and_preserves_them_on_failure(
    tmp_path: Path,
) -> None:
    deployed = _REPO / "automation/rag_ingest/deploy.sh"
    expected = (_REPO / "configs/sensitivity-rules.yaml").read_bytes()

    home, env = _deployer_env(tmp_path / "success")
    legacy = home / ".hermes/sensitivity-rules.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"July rules\n")
    target = home / _DESTINATION
    target.parent.mkdir(parents=True)
    target.write_bytes(b"stale rules\n")
    success = subprocess.run(
        ("bash", str(deployed)), cwd=_REPO, env=env, capture_output=True, text=True,
        check=False, timeout=60,
    )
    assert success.returncode == 0, success.stdout + success.stderr
    assert target.read_bytes() == expected
    assert target.stat().st_mode & 0o777 == 0o600
    assert legacy.read_bytes() == b"July rules\n"
    assert not list(target.parent.glob("sensitivity-rules.yaml.deploy-tmp.*"))

    failure_home, failure_env = _deployer_env(tmp_path / "failure", fail_rules=True)
    failed_target = failure_home / _DESTINATION
    failed_target.parent.mkdir(parents=True)
    failed_target.write_bytes(b"old intact\n")
    failure = subprocess.run(
        ("bash", str(deployed)), cwd=_REPO, env=failure_env, capture_output=True,
        text=True, check=False, timeout=60,
    )
    assert failure.returncode != 0
    assert failed_target.read_bytes() == b"old intact\n"
    assert not list(failed_target.parent.glob("sensitivity-rules.yaml.deploy-tmp.*"))
