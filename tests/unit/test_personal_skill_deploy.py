from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Protocol, TypeAlias, runtime_checkable


class PersonalDeployHarness(Protocol):
    source: Path
    skill_store: Path
    approval_capture: Path

    def deploy(self) -> subprocess.CompletedProcess[str]: ...

    def live_tree_hash(self, index: Path) -> str: ...


@runtime_checkable
class HarnessBuilderModule(Protocol):
    build_personal_deploy_harness: Callable[[Path], PersonalDeployHarness]


def _load_harness_builder() -> HarnessBuilderModule:
    module = import_module("tests.unit.personal_deploy_harness")
    if not isinstance(module, HarnessBuilderModule):
        raise RuntimeError("personal deploy harness builder is unavailable")
    return module


_HARNESS_BUILDER = _load_harness_builder()

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...


_JSON_LOADS: JsonLoader = json.loads


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "automation" / "deploy-skill.sh"


def _deploy(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["HOME"] = str(home)
    environment["DEPLOY_SSH_HOST"] = ""
    return subprocess.run(
        ("bash", str(DEPLOY), *arguments),
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def test_personal_mode_when_repo_is_missing_then_initializes_its_own_git_repo(
    tmp_path: Path,
) -> None:
    # Given
    home = tmp_path / "home"
    source = home / ".hermes" / "personal-skills" / "demo"
    source.mkdir(parents=True)
    _ = (source / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Personal demo skill\n---\n",
        encoding="utf-8",
    )

    # When
    result = _deploy(home, "--personal", "demo")

    # Then
    assert result.returncode == 4
    assert (source / ".git").is_dir()
    assert "DEPLOY-BLOCK" in result.stderr
    assert "stage 1/4" not in result.stderr


def test_personal_mode_when_name_uses_managed_prefix_then_rejects_before_any_stage(
    tmp_path: Path,
) -> None:
    # Given
    home = tmp_path / "home"

    # When
    result = _deploy(home, "--personal", "managed-demo")

    # Then
    assert result.returncode == 4
    assert "MANAGED-BLOCK" in result.stderr
    assert "stage 1/4" not in result.stderr
    assert not (home / ".hermes" / "personal-skills" / "managed-demo").exists()


def test_personal_mode_routes_the_exact_repo_and_sha_through_the_existing_gate() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    assert 'PERSONAL_ROOT="$HOME/.hermes/personal-skills"' in script
    assert 'SRC_DIR="$PERSONAL_ROOT/$SKILL"' in script
    assert 'personal_provenance_check "$SRC_DIR"' in script
    assert '"${PROVENANCE_REQUEST_ARGS[@]}"' in script
    assert script.count('log "stage ') == 4


def test_personal_mode_when_owner_approval_is_simulated_then_mounts_exact_head_tree(
    tmp_path: Path,
) -> None:
    # Given
    harness = _HARNESS_BUILDER.build_personal_deploy_harness(tmp_path)
    head = subprocess.run(
        ("git", "-C", str(harness.source), "rev-parse", "HEAD"),
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    head_tree = subprocess.run(
        ("git", "-C", str(harness.source), "rev-parse", "HEAD^{tree}"),
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()

    # When
    result = harness.deploy()

    # Then
    assert result.returncode == 0, result.stderr
    assert all(f"stage {stage}/4" in result.stderr for stage in range(1, 5))
    approval = _JSON_LOADS(harness.approval_capture.read_text(encoding="utf-8"))
    assert isinstance(approval, dict)
    assert approval["personal_head_sha"] == head
    live = harness.skill_store / "live" / "demo"
    assert live.is_symlink()
    assert harness.live_tree_hash(tmp_path / "live.index") == head_tree
    assert not (live.resolve() / ".git").exists()
