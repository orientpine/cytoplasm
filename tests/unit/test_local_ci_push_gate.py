"""검증되지 않은 push 가 실제로 거부되는가 — 로컬 CI 영수증 게이트.

2026-08-25 실측: 이 저장소는 private + Free 라 `gh api .../branches/main/protection` 이 403 이고
필수 상태 체크를 걸 수 없다. 즉 GitHub CI 는 머지를 막을 권한이 없는 **권고**였고, 실제로 빨간
CI 위에서 머지가 이뤄졌다. 그래서 "PR 전에 CI 를 돌려라"를 산문으로 적으면 그 문장을 읽는 주체가
지키는 만큼만 작동한다 — 배포 provenance 와 ops 체크아웃 커밋 금지가 훅으로 바뀐 것과 같은 이유다.

이 파일이 고정하는 것은 문장이 아니라 **push 가 거부되는가**, 그리고 **영수증이 거짓말을 못 하는가**다.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest
import yaml

_REPO: Final = Path(__file__).resolve().parents[2]
_LOCAL_CI: Final = _REPO / "automation" / "local_ci.sh"
_HOOK: Final = _REPO / "automation" / "hooks" / "pre-push"
_WORKFLOW: Final = _REPO / ".github" / "workflows" / "ci.yml"
_ZERO: Final = "0" * 40

_PASSING_TEST: Final = "def test_ok() -> None:\n    assert True\n"
_FAILING_TEST: Final = "def test_broken() -> None:\n    assert False\n"
_CLEAN_MODULE: Final = "VALUE = 1\n"
_LINT_ERROR_MODULE: Final = "import os\n"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=gate test",
        "-c",
        "user.email=gate@test.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _workspace(tmp_path: Path, *, tests_pass: bool = True, lint_clean: bool = True) -> Path:
    """A miniature checkout shaped like this repository, so the real script runs unchanged."""
    repo = tmp_path / "checkout"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "automation").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        _WORKFLOW.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo / "module.py").write_text(
        _CLEAN_MODULE if lint_clean else _LINT_ERROR_MODULE, encoding="utf-8"
    )
    (repo / "tests" / "unit" / "test_sample.py").write_text(
        _PASSING_TEST if tests_pass else _FAILING_TEST, encoding="utf-8"
    )
    bookkeeping = repo / ".omo" / "senpi-task" / "tasks"
    bookkeeping.mkdir(parents=True)
    (bookkeeping / "session.json").write_text('{"turn": 1}\n', encoding="utf-8")
    for source in (_LOCAL_CI, _HOOK):
        target = repo / "automation" / source.relative_to(_REPO / "automation")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
    _git(repo.parent, "init", "-q", "-b", "work", str(repo))
    _commit(repo, "initial")
    return repo


def _runner_stub(tmp_path: Path) -> tuple[Path, Path]:
    """Stands in for docker so the success path is provable without a container daemon."""
    log = tmp_path / "runner.log"
    stub = tmp_path / "runner-stub"
    stub.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> {log}\nexit 0\n', encoding="utf-8"
    )
    stub.chmod(0o755)
    return stub, log


def _env(state: Path, **extra: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("LOCAL_CI_ALLOW_UNVERIFIED", None)
    environment["LOCAL_CI_STATE_DIR"] = str(state)
    environment.update(extra)
    return environment


def _run(repo: Path, state: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(repo / "automation" / "local_ci.sh"), *args),
        cwd=repo,
        env=_env(state, **extra),
        capture_output=True,
        text=True,
        check=False,
    )


def _push(
    repo: Path, state: Path, local_sha: str, remote_ref: str, **extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(repo / "automation" / "hooks" / "pre-push"), "origin", "git@example.invalid"),
        cwd=repo,
        env=_env(state, **extra),
        input=f"refs/heads/work {local_sha} {remote_ref} {_ZERO}\n",
        capture_output=True,
        text=True,
        check=False,
    )


def _receipts(state: Path) -> list[Path]:
    return sorted(state.glob("*.json")) if state.exists() else []


# --------------------------------------------------------------------------- #
# 영수증은 전 단계가 통과했을 때만 존재한다.


def test_receipt_is_absent_when_the_lint_step_fails(tmp_path: Path) -> None:
    repo = _workspace(tmp_path, lint_clean=False)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)

    result = _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub))

    assert result.returncode != 0, result.stdout + result.stderr
    assert _receipts(state) == []


def test_receipt_is_absent_when_the_unit_tests_fail(tmp_path: Path) -> None:
    repo = _workspace(tmp_path, tests_pass=False)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)

    result = _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub))

    assert result.returncode != 0, result.stdout + result.stderr
    assert _receipts(state) == []


def test_receipt_records_every_step_after_a_clean_run(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    stub, log = _runner_stub(tmp_path)

    result = _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub))

    assert result.returncode == 0, result.stdout + result.stderr
    written = _receipts(state)
    assert len(written) == 1
    receipt = json.loads(written[0].read_text(encoding="utf-8"))
    assert receipt["tree"] == _git(repo, "rev-parse", "HEAD^{tree}")
    assert receipt["commit"] == _git(repo, "rev-parse", "HEAD")
    assert receipt["workflow_sha256"] == hashlib.sha256(
        (repo / ".github" / "workflows" / "ci.yml").read_bytes()
    ).hexdigest()
    assert [step["name"] for step in receipt["steps"]] == ["lint", "unit-tests", "clean-host"]
    assert all(step["rc"] == 0 for step in receipt["steps"])
    assert "python:3.12-slim" in log.read_text(encoding="utf-8")


def test_run_refuses_while_a_tracked_file_is_modified(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")

    result = _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub))

    assert result.returncode != 0
    assert "commit first" in result.stdout + result.stderr
    assert _receipts(state) == []


def test_run_ignores_agent_session_bookkeeping(tmp_path: Path) -> None:
    """`.omo/senpi-task/` 는 하네스가 매 턴 다시 쓰는 추적 파일이고 어떤 검사도 읽지 않는다."""
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)
    (repo / ".omo" / "senpi-task" / "tasks" / "session.json").write_text(
        '{"turn": 2}\n', encoding="utf-8"
    )

    result = _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub))

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_receipts(state)) == 1


def test_receipt_state_lives_outside_the_checkout(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)

    assert _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub)).returncode == 0

    assert state.stat().st_mode & 0o777 == 0o700
    assert _receipts(state)[0].stat().st_mode & 0o777 == 0o600
    assert _git(repo, "status", "--porcelain=v1", "--untracked-files=no") == ""
    receipt = _receipts(state)[0].resolve()
    assert not receipt.is_relative_to(repo.resolve()), "a receipt must never land in the checkout"


# --------------------------------------------------------------------------- #
# 영수증은 내용에 묶인다 — 커밋이 아니라 tree, 그리고 워크플로 자신.


def test_verify_accepts_a_commit_whose_tree_already_passed(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)
    assert _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub)).returncode == 0
    before = _git(repo, "rev-parse", "HEAD")

    _git(
        repo,
        "-c",
        "user.name=gate test",
        "-c",
        "user.email=gate@test.invalid",
        "commit",
        "-q",
        "--amend",
        "-m",
        "reworded, identical tree",
    )
    after = _git(repo, "rev-parse", "HEAD")

    assert after != before
    assert _run(repo, state, "verify", after).returncode == 0


def test_verify_refuses_after_the_tree_changes(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)
    assert _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub)).returncode == 0

    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    changed = _commit(repo, "one character")

    assert _run(repo, state, "verify", changed).returncode != 0


def test_verify_refuses_after_the_workflow_changes(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)
    assert _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub)).returncode == 0
    passed = _git(repo, "rev-parse", "HEAD")
    receipt = _receipts(state)[0]
    stored = json.loads(receipt.read_text(encoding="utf-8"))

    stored["workflow_sha256"] = hashlib.sha256(b"a different workflow").hexdigest()
    receipt.write_text(json.dumps(stored), encoding="utf-8")

    assert _run(repo, state, "verify", passed).returncode != 0


def test_verify_refuses_when_no_receipt_exists(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"

    result = _run(repo, state, "verify", _git(repo, "rev-parse", "HEAD"))

    assert result.returncode != 0
    assert "local_ci.sh run" in result.stdout + result.stderr


# --------------------------------------------------------------------------- #
# 훅이 실제로 push 를 막는다.


def test_hook_refuses_a_branch_push_without_a_receipt(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"

    result = _push(repo, state, _git(repo, "rev-parse", "HEAD"), "refs/heads/work")

    assert result.returncode == 1
    assert "local_ci.sh run" in result.stderr


def test_hook_allows_a_branch_push_with_a_valid_receipt(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)
    assert _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub)).returncode == 0

    result = _push(repo, state, _git(repo, "rev-parse", "HEAD"), "refs/heads/work")

    assert result.returncode == 0, result.stderr


def test_hook_ignores_a_branch_deletion(tmp_path: Path) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"

    result = _push(repo, state, _ZERO, "refs/heads/work")

    assert result.returncode == 0, result.stderr


def test_hook_lets_through_a_branch_cut_before_the_gate_existed(tmp_path: Path) -> None:
    """판정 불가는 위반이 아니다 — 게이트를 모르는 브랜치에는 리포 안에 구제 수단이 없다."""
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    (repo / "automation" / "local_ci.sh").unlink()
    legacy = _commit(repo, "a branch that predates the gate")

    result = _push(repo, state, legacy, "refs/heads/work")

    assert result.returncode == 0, result.stderr
    assert "predates" in result.stderr, "an unverified push must never be silent"


def test_hook_refuses_when_the_pushed_tree_carries_the_gate_but_the_checkout_lost_it(
    tmp_path: Path,
) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    carrying = _git(repo, "rev-parse", "HEAD")
    (repo / "automation" / "local_ci.sh").unlink()

    result = _push(repo, state, carrying, "refs/heads/work")

    assert result.returncode == 1
    assert "carries the gate" in result.stderr


def test_hook_does_not_gate_a_tag_push(tmp_path: Path) -> None:
    """릴리스 태그는 이미 착지한 커밋을 가리킨다 — 여기서 막으면 프로덕션이 언다."""
    repo = _workspace(tmp_path)
    state = tmp_path / "state"

    result = _push(repo, state, _git(repo, "rev-parse", "HEAD"), "refs/tags/v1.0.99")

    assert result.returncode == 0, result.stderr


def test_hook_allows_an_unverified_push_only_through_the_named_escape_hatch(
    tmp_path: Path,
) -> None:
    repo = _workspace(tmp_path)
    state = tmp_path / "state"

    result = _push(
        repo,
        state,
        _git(repo, "rev-parse", "HEAD"),
        "refs/heads/work",
        LOCAL_CI_ALLOW_UNVERIFIED="1",
    )

    assert result.returncode == 0
    assert "LOCAL_CI_ALLOW_UNVERIFIED" in result.stderr, "the bypass must announce itself"


def test_hook_still_refuses_a_session_worktree_pushing_main(tmp_path: Path) -> None:
    """이미 있던 불변식 — 영수증 게이트가 얹혀도 main 직접 push 거부는 그대로다."""
    repo = _workspace(tmp_path)
    state = tmp_path / "state"
    stub, _ = _runner_stub(tmp_path)
    assert _run(repo, state, "run", LOCAL_CI_CONTAINER_RUNNER=str(stub)).returncode == 0
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "-b", "session", str(linked))

    result = subprocess.run(
        ("bash", str(repo / "automation" / "hooks" / "pre-push"), "origin", "git@example.invalid"),
        cwd=linked,
        env=_env(state),
        input=f"refs/heads/session {_git(repo, 'rev-parse', 'HEAD')} refs/heads/main {_ZERO}\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "cannot push refs/heads/main" in result.stderr


# --------------------------------------------------------------------------- #
# 로컬 세트가 GitHub 세트에서 갈라지지 않는다.


#: 로컬에서 **실행하면 안 되는** CI 명령과 그 사유. 예외는 스크립트 주석이 아니라 여기에 둔다 —
#: 주석에 적으면 grep 은 통과하고 동작은 없는 상태가 조용히 만들어진다.
_LOCAL_ONLY: Final = {
    "python3 -m pip install --disable-pip-version-check -r requirements-dev.txt": (
        "the runner installs pinned tools into a disposable image; doing that locally would "
        "mutate the developer's own environment, so local_ci.sh records the tool versions it "
        "actually used in the receipt instead"
    ),
}


def _workflow_invocations() -> set[str]:
    """`run:` 블록에서 실행 가능한 호출만 뽑는다 — 산문 대조가 아니라 명령 대조."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    invocations: set[str] = set()
    for job in workflow["jobs"].values():
        container = job.get("container")
        if isinstance(container, str):
            invocations.add(container)
        for step in job.get("steps", []):
            for line in str(step.get("run", "")).splitlines():
                stripped = line.strip()
                if stripped.startswith(("ruff ", "python -m ", "python3 -m ")):
                    invocations.add(stripped.replace("python -m ", "python3 -m ", 1))
    return invocations


def test_local_ci_runs_every_command_the_workflow_declares() -> None:
    script = _LOCAL_CI.read_text(encoding="utf-8")
    invocations = _workflow_invocations()

    assert invocations, "the workflow declared no runnable command — the extractor is broken"
    missing = sorted(one for one in invocations - set(_LOCAL_ONLY) if one not in script)
    assert not missing, (
        "automation/local_ci.sh no longer runs what .github/workflows/ci.yml runs; "
        f"add each of these to the local set, or exempt it in _LOCAL_ONLY: {missing}"
    )


def test_every_local_only_exemption_still_describes_a_real_workflow_command() -> None:
    stale = sorted(set(_LOCAL_ONLY) - _workflow_invocations())

    assert not stale, (
        "_LOCAL_ONLY exempts command(s) the workflow no longer runs; drop them so the "
        f"exemption list cannot hide a future gap: {stale}"
    )


def test_workflow_no_longer_reruns_the_same_tree_on_main_push() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))

    triggers = workflow[True] if True in workflow else workflow["on"]

    assert "pull_request" in triggers
    assert "push" not in triggers, (
        "a push trigger re-verifies the tree the pull_request run already verified; "
        "that duplicate was measured at 48 of the last 100 runs"
    )


@pytest.mark.skipif(sys.platform != "linux", reason="the gate ships on the linux node")
def test_the_shipped_script_and_hook_are_executable() -> None:
    assert os.access(_LOCAL_CI, os.X_OK)
    assert os.access(_HOOK, os.X_OK)
