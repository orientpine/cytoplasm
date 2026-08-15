from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, runtime_checkable

from automation.node_config import default_node_config, node_config_values


@runtime_checkable
class _FakeWriters(Protocol):
    write_executable: Callable[[Path, str], None]
    write_fake_python: Callable[[Path], None]
    write_fake_sudo: Callable[[Path], None]


def _load_fake_writers() -> _FakeWriters:
    module = import_module("tests.unit.personal_deploy_fakes")
    if not isinstance(module, _FakeWriters):
        raise RuntimeError("personal deploy fake writers are incomplete")
    return module


_FAKES = _load_fake_writers()


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "automation" / "deploy-skill.sh"


@dataclass(frozen=True, slots=True)
class PersonalDeployHarness:
    home: Path
    source: Path
    skill_store: Path
    approval_capture: Path
    environment: Mapping[str, str]

    def deploy(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("bash", str(DEPLOY), "--personal", "demo"),
            capture_output=True,
            check=False,
            env=self.environment,
            text=True,
            timeout=60,
        )

    def live_tree_hash(self, index: Path) -> str:
        live = (self.skill_store / "live" / "demo").resolve()
        environment = {**os.environ, "GIT_INDEX_FILE": str(index)}

        def git(*arguments: str) -> str:
            completed = subprocess.run(
                ("git", "-C", str(self.source), f"--work-tree={live}", *arguments),
                capture_output=True,
                check=True,
                env=environment,
                text=True,
            )
            return completed.stdout.strip()

        _ = git("read-tree", "--empty")
        _ = git("add", "--all", "--", ".")
        return git("write-tree")


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *arguments),
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def _author_personal_skill(home: Path) -> Path:
    source = home / ".hermes" / "personal-skills" / "demo"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    _ = (source / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Personal deployment integration skill\n---\n",
        encoding="utf-8",
    )
    scenario = scripts / "scenario.sh"
    _ = scenario.write_text(
        "#!/usr/bin/env bash\necho SCENARIO-PASS personal-demo\n",
        encoding="utf-8",
    )
    scenario.chmod(0o755)
    _ = _git(source, "init", "-b", "main")
    _ = _git(source, "config", "user.email", "personal@test.local")
    _ = _git(source, "config", "user.name", "personal")
    _ = _git(source, "config", "commit.gpgsign", "false")
    _ = _git(source, "add", "SKILL.md", "scripts/scenario.sh")
    _ = _git(source, "commit", "-m", "Author personal skill")
    return source


def _write_node_config(home: Path, tmp_path: Path, release_current: Path) -> tuple[Path, ...]:
    agent_home = tmp_path / "accounts" / "agent"
    peer_home = tmp_path / "accounts" / "peer"
    ops_home = tmp_path / "accounts" / "ops"
    service_root = tmp_path / "srv"
    skill_store = service_root / "skills"
    values = dict(node_config_values(default_node_config()))
    values.update(
        {
            "deploy_ssh_host": "",
            "operator_account": "operator",
            "agent_account": "agent",
            "peer_account": "peer",
            "ops_account": "ops",
            "service_group": "autophagy",
            "service_root": str(service_root),
            "deploy_checkout": str(ROOT),
            "release_current": str(release_current),
            "release_store": str(service_root / "agent-releases"),
            "private_root": str(service_root / "private"),
            "skill_store": str(skill_store),
            "repair_work": str(service_root / "repair-work"),
            "repair_report_queue": str(service_root / "repair-report-queue"),
            "repair_report_ack": str(service_root / "repair-report-ack"),
            "repair_capability": str(service_root / "repair-capability"),
            "libexec_dir": str(service_root / "libexec"),
            "agent_home": str(agent_home),
            "peer_home": str(peer_home),
            "ops_home": str(ops_home),
        }
    )
    config = home / ".hermes" / "node.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    _ = config.write_text(
        "".join(
            f"{name} = {'false' if name == 'require_signed_updates' else json.dumps(value)}\n"
            for name, value in values.items()
        ),
        encoding="utf-8",
    )
    service_root.mkdir(parents=True)
    return agent_home, peer_home, ops_home, skill_store


def _write_account_state(
    account_homes: tuple[Path, Path, Path],
    skill_store: Path,
) -> None:
    agent_home, peer_home, _ = account_homes
    for account_home in account_homes:
        hermes = account_home / ".hermes"
        (hermes / "interop_runtime").mkdir(parents=True)
        _ = (account_home / ".env.secrets").write_text("\n", encoding="utf-8")
    live = skill_store / "live"
    live.mkdir(parents=True)
    (agent_home / ".hermes" / "skills").symlink_to(live, target_is_directory=True)
    runtime_python = peer_home / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.symlink_to(sys.executable)


def _write_release(tmp_path: Path) -> tuple[Path, Path]:
    release = tmp_path / "release"
    automation = release / "automation"
    automation.mkdir(parents=True)
    _FAKES.write_executable(
        automation / "converge-release-runtime.sh",
        """
        #!/usr/bin/env bash
        exit 0
        """,
    )
    _ = (automation / "skill_library_abi.py").write_text(
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    current = tmp_path / "release-current"
    current.symlink_to(release, target_is_directory=True)
    return release, current


def build_personal_deploy_harness(tmp_path: Path) -> PersonalDeployHarness:
    home = tmp_path / "home"
    source = _author_personal_skill(home)
    _, release_current = _write_release(tmp_path)
    agent_home, peer_home, ops_home, skill_store = _write_node_config(
        home, tmp_path, release_current
    )
    _write_account_state((agent_home, peer_home, ops_home), skill_store)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _FAKES.write_fake_sudo(bin_dir)
    _FAKES.write_fake_python(bin_dir)
    _FAKES.write_executable(
        bin_dir / "hermes",
        """
        #!/usr/bin/env bash
        printf '│ demo │\\n'
        """,
    )
    capture = tmp_path / "approval-record.json"
    environment = {
        **os.environ,
        "DEPLOY_SSH_HOST": "",
        "E2E_TEST_MODE": "1",
        "FAKE_AGENT_HOME": str(agent_home),
        "FAKE_APPROVAL_CAPTURE": str(capture),
        "FAKE_OPS_HOME": str(ops_home),
        "FAKE_PEER_HOME": str(peer_home),
        "FAKE_SKILL_STORE": str(skill_store),
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "REAL_PYTHON": sys.executable,
    }
    return PersonalDeployHarness(home, source, skill_store, capture, environment)
