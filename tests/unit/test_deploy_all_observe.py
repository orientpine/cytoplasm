"""v2 배포 선언의 노드 관측(`deploy_all_observe`) — 트리·빌드·cron·roster·위임 프로브.

판정 축은 하나다: 보지 못한 것은 깨끗한 것이 아니다. 읽기 실패는 `unknown`(부재 아님),
잘린 cron 목록은 빈 목록이 아니고, 위임 프로브를 못 돌리면 `pending-unknown` 한 줄이다.
노드 대신 임시 계정 홈과 가짜 `hermes` 를 쓰고, 실행기는 주입한다 — 원격 고정 스크립트
(`REMOTE_SCRIPT`)는 sudo 없이 같은 인자로 로컬 bash 에서 실제로 돈다.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

observe = importlib.import_module("automation.deploy_all_observe")

_HELPER: Final = "skills/mail/scripts/mailon_vendor_digest.sh"
_LISTING_HEAD: Final = (
    "\n┌──────┐\n│                         Scheduled Jobs                                  │\n"
    "└──────┘\n\n"
)

Runner = Callable[[str, tuple[str, ...]], tuple[int, str]]


def _job(job_id: str, name: str, schedule: str, script: str, deliver: str = "local") -> str:
    return (
        f"  {job_id} [active]\n"
        f"    Name:      {name}\n"
        f"    Schedule:  {schedule}\n"
        "    Repeat:    ∞\n"
        "    Next run:  2026-10-02T09:00:00+09:00\n"
        f"    Deliver:   {deliver}\n"
        f"    Script:    {script}\n"
        "    Mode:      no-agent (script stdout delivered directly)\n"
        "    Last run:  2026-10-01T09:00:40+09:00  ok\n"
        "\n"
    )


def _runtime(tmp_path: Path, rows: str, package: str = "automation/pkg") -> Path:
    root = tmp_path / "runtime"
    (root / package).mkdir(parents=True, exist_ok=True)
    _ = (root / package / "deploy-manifest.txt").write_text(rows, encoding="utf-8")
    helper = root / _HELPER
    helper.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copy(_REPO / _HELPER, helper)
    return root


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _local_runner(homes: Path, fake_bin: Path | None = None) -> Runner:
    """sudo 대신 계정 홈을 HOME 으로 주고 같은 고정 스크립트를 로컬 bash 로 돌린다."""

    def run(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        home = homes / account
        home.mkdir(parents=True, exist_ok=True)
        path = os.environ["PATH"] if fake_bin is None else f"{fake_bin}:{os.environ['PATH']}"
        proc = subprocess.run(
            ("bash", "-c", observe.REMOTE_SCRIPT, "_", *args),
            env={"HOME": str(home), "PATH": path},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return proc.returncode, proc.stdout

    return run


def _scripted(answers: dict[tuple[str, str], tuple[int, str]], fallback: Runner | None = None) -> Runner:
    """(계정, 첫 인자) → 응답. 지정이 없으면 fallback, 그것도 없으면 실행 불가."""

    def run(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        key = (account, args[0])
        if key in answers:
            return answers[key]
        if fallback is not None:
            return fallback(account, args)
        return 1, ""

    return run


def _artifacts(lines: list[str]) -> dict[tuple[str, str], list[str]]:
    rows = [line.split("|") for line in lines if line.startswith("OBS|artifact|")]
    assert all(len(row) == 11 for row in rows)
    return {(row[3], row[4]): row for row in rows}


def _details(lines: list[str], kind: str, account: str, destination: str) -> list[tuple[str, str]]:
    """한 선언 행(종류, 계정, 목적지)의 세부 줄 — 같은 목적지의 다른 계정 줄은 섞지 않는다."""
    found: list[tuple[str, str]] = []
    for line in lines:
        parts = line.split("|")
        if parts[1] == "artifact-detail" and (parts[2], parts[3], parts[4]) == (kind, account, destination):
            assert len(parts) == 7
            found.append((parts[5], parts[6]))
    return found


def _status(lines: list[str], account: str, destination: str) -> str:
    return _artifacts(lines)[(account, destination)][8]


def _tree_rows(tmp_path: Path, attrs: str = "") -> Path:
    root = _runtime(tmp_path, f"agent|automation/pkg|.hermes/pkg_runtime|v2:tree{attrs}\n")
    _write(root / "automation/pkg/a.py", "a = 1\n")
    _write(root / "automation/pkg/sub/b.py", "b = 2\n")
    return root


def _deploy_copy(root: Path, homes: Path, names: list[str], dest: str = ".hermes/pkg_runtime") -> Path:
    target = homes / "agent" / dest
    for name in names:
        _write(target / name, (root / "automation/pkg" / name).read_text(encoding="utf-8"))
    return target


def _collect(root: Path, runner: Runner, ops: str = "ops") -> list[str]:
    def guarded(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        if args[0] == "delegated":
            return 0, "DELEGATED-END|0\n"
        return runner(account, args)

    return observe.collect(root, guarded, ops)




def test_snapshot_is_order_independent(tmp_path: Path) -> None:
    one = {"b.py": "1" * 64, "a.py": "2" * 64, "z/x.py": "3" * 64}
    two = dict(reversed(list(one.items())))
    assert observe.tree_identity(one) == observe.tree_identity(two)
    root = _tree_rows(tmp_path)
    homes = tmp_path / "homes"
    _ = _deploy_copy(root, homes, ["sub/b.py", "a.py"])
    lines = _collect(root, _local_runner(homes))
    row = _artifacts(lines)[("agent", ".hermes/pkg_runtime")]
    assert row[8] == "ok"
    assert row[9] == row[10] != "?"


def test_tree_profile_covers_non_python_inputs(tmp_path: Path) -> None:
    root = _tree_rows(tmp_path, ";profile=tree")
    _write(root / "automation/pkg/Dockerfile", "FROM scratch\n")
    homes = tmp_path / "homes"
    target = _deploy_copy(root, homes, ["a.py", "sub/b.py", "Dockerfile", "deploy-manifest.txt"])
    assert _status(_collect(root, _local_runner(homes)), "agent", ".hermes/pkg_runtime") == "ok"
    _write(target / "Dockerfile", "FROM busybox\n")
    lines = _collect(root, _local_runner(homes))
    assert _status(lines, "agent", ".hermes/pkg_runtime") == "stale"
    assert _details(lines, "tree", "agent", ".hermes/pkg_runtime") == [("different-file", "Dockerfile")]


def test_cron_matching_job_is_ok(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "agent|automation/pkg/cron/w.py|w-watch|v2:cron;schedule=*/5  * * * *;script=w.py;"
        "deliver=local;mode=no-agent\n",
    )
    homes = tmp_path / "homes"
    fake_bin = tmp_path / "bin"
    _write(fake_bin / "hermes", '#!/usr/bin/env bash\ncat "$HOME/listing.txt"\n')
    (fake_bin / "hermes").chmod(0o755)
    _write(homes / "agent/listing.txt", _LISTING_HEAD + _job("5dd6adf15063", "w-watch", "*/5 * * * *", "w.py"))
    lines = _collect(root, _local_runner(homes, fake_bin))
    row = _artifacts(lines)[("agent", "w-watch")]
    assert row[2] == "cron" and row[8] == "ok" and row[9] == row[10]


def test_derived_identity_matches_shell_helper(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "agent|skills/mail/vendor/mailon|.hermes/mailon-runtime/current|v2:derived;"
        "algorithm=mailon-py-v1;requirements=skills/mail/vendor/requirements.txt\n",
        package="skills/mail",
    )
    _write(root / "skills/mail/vendor/mailon/__init__.py", "x = 1\n")
    _write(root / "skills/mail/vendor/mailon/sub/m.py", "y = 2\n")
    _write(root / "skills/mail/vendor/requirements.txt", "pyotp==2.9.0\n")
    shell = subprocess.run(
        ("bash", "-c", '. "$1"; mailon_vendor_digest "$2"', "_", str(root / _HELPER),
         str(root / "skills/mail/vendor/mailon")),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    req16 = hashlib.sha256((root / "skills/mail/vendor/requirements.txt").read_bytes()).hexdigest()[:16]
    homes = tmp_path / "homes"
    current = homes / "agent/.hermes/mailon-runtime/current"
    shutil.copytree(root / "skills/mail/vendor/mailon", current / "mailon")
    _write(current / "runtime-manifest.json", json.dumps({"src_digest": shell, "req_digest": req16}))
    lines = _collect(root, _local_runner(homes))
    row = _artifacts(lines)[("agent", ".hermes/mailon-runtime/current")]
    assert row[9] == f"{shell}-{req16}" == row[10]
    assert row[8] == "ok"




def test_runtime_extras_are_not_selection_filtered(tmp_path: Path) -> None:
    root = _tree_rows(tmp_path, ";files=a.py")
    homes = tmp_path / "homes"
    _ = _deploy_copy(root, homes, ["a.py", "sub/b.py"])  # b.py 는 files= 밖에 남은 옛 파일
    lines = _collect(root, _local_runner(homes))
    assert _status(lines, "agent", ".hermes/pkg_runtime") == "extra"
    assert _details(lines, "tree", "agent", ".hermes/pkg_runtime") == [("extra-file", "sub/b.py")]


def test_cron_exclusion_is_release_side_only(tmp_path: Path) -> None:
    root = _tree_rows(tmp_path)
    _write(root / "automation/pkg/cron/w_watch.py", "print()\n")
    homes = tmp_path / "homes"
    _ = _deploy_copy(root, homes, ["a.py", "sub/b.py"])
    assert _status(_collect(root, _local_runner(homes)), "agent", ".hermes/pkg_runtime") == "ok"
    _ = _deploy_copy(root, homes, ["cron/w_watch.py"])
    lines = _collect(root, _local_runner(homes))
    assert _status(lines, "agent", ".hermes/pkg_runtime") == "extra"
    assert _details(lines, "tree", "agent", ".hermes/pkg_runtime") == [("extra-file", "cron/w_watch.py")]


def test_permission_error_is_unknown_not_absent(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "agent|automation/pkg/a.py|.hermes/locked/a.py|v2:file\n"
        "agent|automation/pkg|.hermes/locked/tree|v2:tree;policy=optional;reason=r\n",
    )
    _write(root / "automation/pkg/a.py", "a = 1\n")
    homes = tmp_path / "homes"
    locked = homes / "agent/.hermes/locked"
    locked.mkdir(parents=True)
    locked.chmod(0o000)
    try:
        lines = _collect(root, _local_runner(homes))
    finally:
        locked.chmod(0o700)
    assert _status(lines, "agent", ".hermes/locked/a.py") == "unknown"
    assert _artifacts(lines)[("agent", ".hermes/locked/a.py")][10] == "?"
    assert _status(lines, "agent", ".hermes/locked/tree") == "unknown"
    sudo_refused = _collect(root, _scripted({}))
    assert _status(sudo_refused, "agent", ".hermes/locked/a.py") == "unknown"


def test_missing_selected_source_is_unknown(tmp_path: Path) -> None:
    root = _tree_rows(tmp_path, ";files=a.py,gone.py")
    homes = tmp_path / "homes"
    _ = _deploy_copy(root, homes, ["a.py"])
    lines = _collect(root, _local_runner(homes))
    row = _artifacts(lines)[("agent", ".hermes/pkg_runtime")]
    assert row[8] == "unknown" and row[9] == "?"


def test_duplicate_cron_names_are_unknown(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "agent|automation/pkg/cron/w.py|w-watch|v2:cron;schedule=*/5 * * * *;script=w.py;"
        "deliver=local;mode=no-agent\n",
    )
    listing = _LISTING_HEAD + _job("aaaaaaaaaaaa", "w-watch", "*/5 * * * *", "w.py") + _job(
        "bbbbbbbbbbbb", "w-watch", "*/5 * * * *", "w.py"
    )
    lines = _collect(root, _scripted({("agent", "cron"): (0, listing)}))
    assert _status(lines, "agent", "w-watch") == "unknown"
    assert _details(lines, "cron", "agent", "w-watch") == [("duplicate-name", "-")]


@pytest.mark.parametrize(
    "listing",
    (
        "",  # 아무것도 오지 않았다 — 빈 목록이 아니다
        _LISTING_HEAD + _job("aaaaaaaaaaaa", "other", "0 9 * * *", "o.py")[:120],  # 블록 중간 절단
        _LISTING_HEAD + _job("aaaaaaaaaaaa", "w-watch", "*/5 * * * *", "w.py").rstrip("\n"),
        _job("aaaaaaaaaaaa", "w-watch", "*/5 * * * *", "w.py"),  # 머리말 없음
        _LISTING_HEAD + _job("aaaaaaaaaaaa", "w-watch", "*/5 * * * *", "w.py").replace(
            "    Last run:", "    Name:      injected\n    Last run:"
        ),  # 블록 안 같은 필드 두 번 = 파싱 불가
    ),
)
def test_truncated_cron_listing_is_unknown(tmp_path: Path, listing: str) -> None:
    root = _runtime(
        tmp_path,
        "agent|automation/pkg/cron/w.py|w-watch|v2:cron;schedule=*/5 * * * *;script=w.py;"
        "deliver=local;mode=no-agent\n",
    )
    lines = _collect(root, _scripted({("agent", "cron"): (0, listing)}))
    assert _status(lines, "agent", "w-watch") == "unknown"
    rc_failed = _collect(root, _scripted({("agent", "cron"): (1, _LISTING_HEAD)}))
    assert _status(rc_failed, "agent", "w-watch") == "unknown"


def test_cron_field_drift_is_stale_and_absence_is_absent(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "agent|automation/pkg/cron/w.py|w-watch|v2:cron;schedule=*/2 * * * *;script=w.py;"
        "deliver=discord;mode=no-agent\n"
        "agent|automation/pkg/cron/x.py|x-watch|v2:cron;schedule=0 9 * * *;script=x.py;"
        "deliver=local;mode=no-agent\n",
    )
    listing = _LISTING_HEAD + _job("aaaaaaaaaaaa", "w-watch", "*/10 * * * *", "w.py", "discord")
    lines = _collect(root, _scripted({("agent", "cron"): (0, listing)}))
    assert _status(lines, "agent", "w-watch") == "stale"
    assert _details(lines, "cron", "agent", "w-watch") == [("different-schedule", "-")]
    assert _status(lines, "agent", "x-watch") == "absent"


def test_matching_name_does_not_hide_corrupt_runtime(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "agent|skills/mail/vendor/mailon|.hermes/mailon-runtime/current|v2:derived;"
        "algorithm=mailon-py-v1;requirements=skills/mail/vendor/requirements.txt\n",
        package="skills/mail",
    )
    _write(root / "skills/mail/vendor/mailon/__init__.py", "x = 1\n")
    _write(root / "skills/mail/vendor/requirements.txt", "pyotp==2.9.0\n")
    homes = tmp_path / "homes"
    lines = _collect(root, _local_runner(homes))
    want = _artifacts(lines)[("agent", ".hermes/mailon-runtime/current")][9]
    assert _status(lines, "agent", ".hermes/mailon-runtime/current") == "absent"
    src16, req16 = want.split("-")
    current = homes / "agent/.hermes/mailon-runtime/current"
    _write(current / "mailon/__init__.py", "x = 'tampered'\n")  # 이름(manifest)은 맞고 내용이 다르다
    _write(current / "runtime-manifest.json", json.dumps({"src_digest": src16, "req_digest": req16}))
    lines = _collect(root, _local_runner(homes))
    row = _artifacts(lines)[("agent", ".hermes/mailon-runtime/current")]
    assert row[10] == want and row[8] == "stale"
    assert _details(lines, "derived", "agent", ".hermes/mailon-runtime/current") == [("corrupt", "-")]


def test_roster_missing_on_one_account_holds_the_whole_bundle(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "agent|automation/pkg/p.py|.hermes/plugins/p/p.py|v2:file;requires=roster;activation=gateway\n"
        "peer|automation/pkg/p.py|.hermes/plugins/p/p.py|v2:file;requires=roster;activation=gateway\n"
        "agent|automation/pkg/q.py|.hermes/interop/q.py|v2:file\n",
    )
    _write(root / "automation/pkg/p.py", "p = 1\n")
    _write(root / "automation/pkg/q.py", "q = 1\n")
    homes = tmp_path / "homes"
    local = _local_runner(homes)
    roster = {("agent", "roster"): (0, ""), ("peer", "roster"): (1, "")}
    lines = _collect(root, _scripted(roster, local))
    rows = [row for row in (line.split("|") for line in lines) if row[1] == "artifact"]
    held = [(row[3], row[8]) for row in rows if row[4] == ".hermes/plugins/p/p.py"]
    assert held == [("agent", "held"), ("peer", "held")]
    # 계정마다 자기 세부 줄이 있어야 한다 — 통과한 agent 는 peer 의 사유가 아니라 형제 보류다
    assert _details(lines, "file", "agent", ".hermes/plugins/p/p.py") == [("roster-sibling", "-")]
    assert _details(lines, "file", "peer", ".hermes/plugins/p/p.py") == [("roster-required", "-")]
    assert _status(lines, "agent", ".hermes/interop/q.py") == "absent"  # 조건 없는 행은 그대로 판정
    roster[("peer", "roster")] = (0, "")
    lines = _collect(root, _scripted(roster, local))
    assert _status(lines, "peer", ".hermes/plugins/p/p.py") == "absent"


def test_held_row_that_matches_is_ok_and_one_that_differs_is_held(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "agent|automation/pkg/a.py|.hermes/held/a.py|v2:file;policy=held;reason=owner-deployed\n"
        "agent|automation/pkg/b.py|.hermes/held/b.py|v2:file;policy=held;reason=owner-deployed\n"
        "agent|automation/pkg/a.py|.hermes/held/c.py|v2:file;policy=held;reason=owner-deployed\n",
    )
    _write(root / "automation/pkg/a.py", "a = 1\n")
    _write(root / "automation/pkg/b.py", "b = 1\n")
    homes = tmp_path / "homes"
    _write(homes / "agent/.hermes/held/a.py", "a = 1\n")
    _write(homes / "agent/.hermes/held/b.py", "b = 'old'\n")
    lines = _collect(root, _local_runner(homes))
    assert _status(lines, "agent", ".hermes/held/a.py") == "ok"
    assert _status(lines, "agent", ".hermes/held/b.py") == "held"
    assert _details(lines, "file", "agent", ".hermes/held/b.py") == [("different-file", "-")]
    assert _details(lines, "file", "agent", ".hermes/held/c.py") == [("absent", "-")]
    unreadable = _collect(root, _scripted({}))
    assert _status(unreadable, "agent", ".hermes/held/a.py") == "held"
    assert _details(unreadable, "file", "agent", ".hermes/held/a.py") == [("unreadable", "-")]


def test_retired_and_optional_rows(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "agent|automation/pkg|.hermes/old_runtime|v2:tree;policy=retired;reason=r\n"
        "agent|automation/pkg|.hermes/gone_runtime|v2:tree;policy=retired;reason=r\n"
        "agent|automation/pkg/a.py|.hermes/opt/a.py|v2:file;policy=optional;reason=r\n",
    )
    _write(root / "automation/pkg/a.py", "a = 1\n")
    homes = tmp_path / "homes"
    _write(homes / "agent/.hermes/old_runtime/x.py", "x\n")
    lines = _collect(root, _local_runner(homes))
    assert _status(lines, "agent", ".hermes/old_runtime") == "retired-present"
    assert _status(lines, "agent", ".hermes/gone_runtime") == "retired-absent"
    assert _status(lines, "agent", ".hermes/opt/a.py") == "ok"
    assert _status(_collect(root, _scripted({})), "agent", ".hermes/old_runtime") == "unknown"


def test_rag_node_rows_emit_no_line(tmp_path: Path) -> None:
    root = _runtime(
        tmp_path,
        "ops|automation/pkg|personal-rag|v2:tree;policy=held;reason=window;profile=tree;node=rag\n",
    )
    calls: list[tuple[str, tuple[str, ...]]] = []

    def record(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        calls.append((account, args))
        return 1, ""

    lines = _collect(root, record)
    assert [line for line in lines if line.startswith(("OBS|artifact|", "OBS|artifact-detail|"))] == []
    assert lines[-1] == "OBS|artifacts|0"
    assert calls == []


def test_artifact_count_matches_the_artifact_lines(tmp_path: Path) -> None:
    root = _tree_rows(tmp_path)
    lines = _collect(root, _local_runner(tmp_path / "homes"))
    assert lines[-1] == f"OBS|artifacts|{sum(1 for x in lines if x.startswith('OBS|artifact|'))}"
    assert lines[-1] == "OBS|artifacts|1"




def test_a_fresh_helper_drift_is_reported_without_any_prior_incident(tmp_path: Path) -> None:
    """사건 상태 파일이 아예 없는 노드에서도 릴리스 시점의 어긋남이 실린다."""
    root = _runtime(tmp_path, "agent|automation/pkg/a.py|.hermes/a.py|v2:file\n")
    _write(root / "automation/pkg/a.py", "a\n")
    _install_delegated(root)  # 실제 릴리스 트리는 위임 스크립트를 싣는다
    state = tmp_path / "private"
    assert not state.exists()
    out = (
        "DELEGATED|release_helper_drift|node1|FAIL|node1 privileged release helpers match release\n"
        "DELEGATED-DETAIL|release_helper_drift|node1|[release-helper] HELPER-DRIFT: x is not installed\n"
        "DELEGATED-DETAIL|release_helper_drift|node1|[release-helper] re-run the provisioner on the "
        "node: sudo bash <release>/automation/provision-deploy-converge.sh\n"
        "DELEGATED|healthcheck_wrapper_current|node1|PASS|node1 healthcheck probe allowlist matches the checks\n"
        "DELEGATED-END|2\n"
    )
    seen: list[tuple[str, tuple[str, ...]]] = []

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        seen.append((account, args))
        return (0, out) if args[0] == "delegated" else (3, "")

    lines = observe.collect(root, runner, "ops")
    assert ("ops", ("delegated", str(root))) in seen
    pending = [line for line in lines if line.startswith("OBS|pending")]
    assert pending == [
        "OBS|pending|release_helper_drift|node1|FAIL|node1 privileged release helpers match release",
        "OBS|pending-detail|release_helper_drift|node1|[release-helper] HELPER-DRIFT: x is not installed",
        "OBS|pending-detail|release_helper_drift|node1|[release-helper] re-run the provisioner on the "
        "node: sudo bash <release>/automation/provision-deploy-converge.sh",
        "OBS|pending|healthcheck_wrapper_current|node1|PASS|node1 healthcheck probe allowlist matches the checks",
    ]
    assert lines[-1] == "OBS|artifacts|1"


@pytest.mark.parametrize(
    ("rc", "out"),
    (
        (1, "DELEGATED-END|0\n"),  # 스크립트 실패
        (124, ""),  # timeout 300 이 죽였다
        (0, "DELEGATED|release_helper_drift|n|FAIL|x\nDELEGATED-END|2\n"),  # 수 불일치
        (0, "DELEGATED|release_helper_drift|n|FAIL|x\n"),  # END 없음
        (0, ""),  # 빈 출력은 "전부 통과" 가 아니다
        (0, "DELEGATED|release_helper_drift|n|MAYBE|x\nDELEGATED-END|1\n"),  # 모르는 상태
        (0, "stray line\nDELEGATED-END|0\n"),  # 형식 밖 줄
    ),
)
def test_delegated_probes_that_cannot_run_are_unknown_not_empty(
    tmp_path: Path, rc: int, out: str
) -> None:
    root = _runtime(tmp_path, "agent|automation/pkg/a.py|.hermes/a.py|v2:file\n")
    _write(root / "automation/pkg/a.py", "a\n")
    _install_delegated(root)  # 스크립트가 있어야 파서가 실행기 출력을 실제로 판정한다
    lines = observe.collect(
        root, lambda _a, args: (rc, out) if args[0] == "delegated" else (3, ""), "ops"
    )
    pending = [line for line in lines if line.startswith("OBS|pending")]
    assert pending == [observe.UNAVAILABLE]


def test_delegated_script_end_to_end_through_the_fixed_script(tmp_path: Path) -> None:
    """실제 healthcheck_delegated.sh 를 고정 스크립트의 `delegated` 갈래로 돌린다."""
    root = _runtime(tmp_path, "agent|automation/pkg/a.py|.hermes/a.py|v2:file\n")
    _write(root / "automation/pkg/a.py", "a\n")
    _ = shutil.copy(_REPO / "automation/healthcheck_delegated.sh", root / "automation")
    entry = tmp_path / "entry.sh"
    _write(
        entry,
        'LIVE_CHECKS=("n1 helpers|release_helper_drift|node1|ops|t")\n'
        "run_check() { echo 'HELPER-DRIFT-UNKNOWN: unreadable path=/x' >&2; return 1; }\n",
    )
    homes = tmp_path / "homes"
    local = _local_runner(homes)

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        if args[0] != "delegated":
            return local(account, args)
        proc = subprocess.run(
            ("bash", "-c", observe.REMOTE_SCRIPT, "_", *args),
            env={"HOME": str(homes), "PATH": os.environ["PATH"],
                 "HEALTHCHECK_DELEGATED_ENTRY": str(entry)},
            capture_output=True, text=True, check=False, timeout=60,
        )
        return proc.returncode, proc.stdout

    lines = observe.collect(root, runner, "ops")
    assert [line for line in lines if line.startswith("OBS|pending")] == [
        "OBS|pending|release_helper_drift|node1|UNKNOWN|n1 helpers",
        "OBS|pending-detail|release_helper_drift|node1|HELPER-DRIFT-UNKNOWN: unreadable path=/x",
    ]




def test_unsafe_arguments_never_reach_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    for account, args in (
        ("agent; rm -rf /", ("sha", ".hermes/x")),
        ("agent", ("sha", ".hermes/$(touch pwned)")),
        ("agent", ("sha", ".hermes/a b")),
        ("agent", ("sha", ".hermes/a\nb")),
        ("agent", ("sha", ".hermes/a|b")),
    ):
        assert observe.default_runner(account, args) == (64, "")
    assert calls == []


def test_untrusted_node_text_cannot_break_the_line_grammar(tmp_path: Path) -> None:
    root = _tree_rows(tmp_path)
    homes = tmp_path / "homes"
    target = _deploy_copy(root, homes, ["a.py", "sub/b.py"])
    _write(target / "evil|x;$(touch pwned)\nOBS|end.py", "x\n")
    _install_delegated(root)
    delegated = (
        "DELEGATED|release_helper_drift|n|FAIL|name|with|pipes\n"
        "DELEGATED-DETAIL|release_helper_drift|n|a|b\rOBS|end\n"
        "DELEGATED-END|1\n"
    )

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        return (0, delegated) if args[0] == "delegated" else _local_runner(homes)(account, args)

    lines = observe.collect(root, runner, "ops")
    assert not (homes / "agent" / "pwned").exists() and not Path("pwned").exists()
    assert all("\n" not in line and "\r" not in line for line in lines)
    assert "OBS|end" not in lines
    assert _status(lines, "agent", ".hermes/pkg_runtime") == "extra"
    (reason, rel), = _details(lines, "tree", "agent", ".hermes/pkg_runtime")
    assert reason == "extra-file" and "|" not in rel and "$(" not in rel
    pending = [line.split("|") for line in lines if line.startswith("OBS|pending")]
    assert [len(row) for row in pending] == [6, 5]


def test_probe_reports_v2_artifacts_and_skips_v2_file_destinations_as_undeclared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    deploy_all_probe = importlib.import_module("automation.deploy_all_probe")

    root = _runtime(tmp_path, "agent|automation/pkg/a.py|.hermes/scripts/v2.py|v2:file\n")
    _write(root / "automation/pkg/a.py", "a\n")
    _write(root / "automation/pkg/w.py", "w\n")
    _write(
        root / "configs/watcher-deploy-manifest.txt",
        "agent|automation/pkg/w.py|.hermes/scripts/w.py|required\n",
    )
    monkeypatch.setattr(
        deploy_all_probe, "inspect_mounts",
        lambda _r, _l: SimpleNamespace(stale=(), unmounted=(), orphaned=()),
    )
    homes = {".hermes/scripts/w.py": "1" * 64, ".hermes/scripts/v2.py": "2" * 64,
             ".hermes/scripts/hand.py": "3" * 64}
    lines = deploy_all_probe.observations(
        root, tmp_path, lambda _a, d: homes[d], lambda _a: tuple(homes),
        lambda runtime: observe.collect(runtime, lambda _a, _args: (1, ""), "ops"),
    )
    undeclared = [line for line in lines if line.startswith("OBS|undeclared|")]
    assert undeclared == ["OBS|undeclared|agent|.hermes/scripts/hand.py|333333333333"]
    assert lines[-1] == "OBS|end" and lines[-2] == "OBS|artifacts|1"
    assert observe.UNAVAILABLE in lines


def _install_delegated(root: Path) -> None:
    _ = shutil.copy(_REPO / "automation/healthcheck_delegated.sh", root / "automation")


def _counting(answer: tuple[int, str]) -> tuple[Runner, list[tuple[str, ...]]]:
    calls: list[tuple[str, ...]] = []

    def run(_account: str, args: tuple[str, ...]) -> tuple[int, str]:
        calls.append(args)
        return answer if args[0] == "delegated" else (3, "")

    return run, calls


def test_missing_delegated_script_never_calls_the_runner(tmp_path: Path) -> None:
    """스크립트가 없는 런타임은 위임 프로브를 돌릴 수 없다 — sudo 를 부를 이유가 없다."""
    root = _runtime(tmp_path, "agent|automation/pkg/a.py|.hermes/a.py|v2:file\n")
    _write(root / "automation/pkg/a.py", "a\n")
    runner, calls = _counting((0, "DELEGATED-END|0\n"))
    lines = observe.collect(root, runner, "ops")
    assert [args for args in calls if args[0] == "delegated"] == []
    assert [line for line in lines if line.startswith("OBS|pending")] == [observe.UNAVAILABLE]


@pytest.mark.parametrize("shape", ("directory", "unreadable"))
def test_unusable_delegated_script_is_not_run(tmp_path: Path, shape: str) -> None:
    root = _runtime(tmp_path, "agent|automation/pkg/a.py|.hermes/a.py|v2:file\n")
    _write(root / "automation/pkg/a.py", "a\n")
    script = root / "automation/healthcheck_delegated.sh"
    if shape == "directory":
        script.mkdir()
    else:
        _install_delegated(root)
        script.chmod(0)
        if os.access(script, os.R_OK):
            script.chmod(0o755)
            pytest.fail("cannot make the script unreadable as this user")
    runner, calls = _counting((0, "DELEGATED-END|0\n"))
    lines = observe.collect(root, runner, "ops")
    script.chmod(0o755)
    assert [args for args in calls if args[0] == "delegated"] == []
    assert [line for line in lines if line.startswith("OBS|pending")] == [observe.UNAVAILABLE]


def test_present_delegated_script_is_run_exactly_once(tmp_path: Path) -> None:
    """지름길이 실제 노드의 위임 프로브를 끄지 않는다 — FAIL 은 그대로 올라온다."""
    root = _runtime(tmp_path, "agent|automation/pkg/a.py|.hermes/a.py|v2:file\n")
    _write(root / "automation/pkg/a.py", "a\n")
    _install_delegated(root)
    runner, calls = _counting((0, "DELEGATED|release_helper_drift|n|FAIL|x\nDELEGATED-END|1\n"))
    lines = observe.collect(root, runner, "ops")
    assert [args for args in calls if args[0] == "delegated"] == [("delegated", str(root))]
    assert [line for line in lines if line.startswith("OBS|pending")] == [
        "OBS|pending|release_helper_drift|n|FAIL|x"
    ]


def _roster_bundle(tmp_path: Path) -> Path:
    root = _runtime(
        tmp_path,
        "agent|automation/pkg/p.py|.hermes/plugins/p/p.py|v2:file;requires=roster;activation=gateway\n"
        "peer|automation/pkg/p.py|.hermes/plugins/p/p.py|v2:file;requires=roster;activation=gateway\n"
        "agent|automation/pkg/p.py|.hermes/plugins/p/q.py|v2:file;requires=roster;activation=gateway\n",
    )
    _write(root / "automation/pkg/p.py", "p = 1\n")
    return root


def _roster_reasons(lines: list[str]) -> list[tuple[str, str, list[tuple[str, str]]]]:
    rows = [row for row in (line.split("|") for line in lines) if row[1] == "artifact"]
    return [(row[3], row[8], _details(lines, row[2], row[3], row[4])) for row in rows]


@pytest.mark.parametrize("rc", (44, 64, 124, 126, 127))
def test_roster_validation_that_could_not_run_is_unverified(tmp_path: Path, rc: int) -> None:
    root = _roster_bundle(tmp_path)
    roster = {("agent", "roster"): (0, ""), ("peer", "roster"): (rc, "")}
    lines = _collect(root, _scripted(roster, _local_runner(tmp_path / "homes")))
    reasons = _roster_reasons(lines)
    assert [(account, status) for account, status, _ in reasons] == [
        ("agent", "held"), ("peer", "held"), ("agent", "held")
    ]
    # 통과한 agent 는 peer 의 사유를 물려받지 않는다 — 다른 계정 때문에 보류됐다고만 적는다
    assert [found for _, _, found in reasons] == [
        [("roster-sibling", "-")], [("roster-unverified", "-")], [("roster-sibling", "-")]
    ]


@pytest.mark.parametrize("rc", (1, 2))
def test_roster_reason_is_per_account_when_one_ran_and_failed_and_the_other_could_not_run(
    tmp_path: Path, rc: int
) -> None:
    root = _roster_bundle(tmp_path)
    roster = {("agent", "roster"): (124, ""), ("peer", "roster"): (rc, "")}
    lines = _collect(root, _scripted(roster, _local_runner(tmp_path / "homes")))
    reasons = _roster_reasons(lines)
    assert [(account, status) for account, status, _ in reasons] == [
        ("agent", "held"), ("peer", "held"), ("agent", "held")
    ]
    # 사유는 계정마다다 — 검증이 돌지 못한 agent(124)는 roster-required 를 물려받지 않는다
    assert [found for _, _, found in reasons] == [
        [("roster-unverified", "-")], [("roster-required", "-")], [("roster-unverified", "-")]
    ]


def _mixed_bundle_receipt(lines: list[str]) -> dict[str, object]:
    from automation import deploy_receipt
    from automation.deploy_all import parse_observations

    home = "OBS|home|agent|.hermes/scripts/w.py|automation/w/w.py|required|" + "a" * 64 + "|" + "a" * 64
    plan = parse_observations(["OBS|release|abc123", "OBS|mounts|judged", home, *lines, "OBS|end"])
    return json.loads(deploy_receipt.render_receipt(plan, verified_at="2026-10-01T00:00:00+00:00"))


def _assert_mixed_reasons(lines: list[str]) -> None:
    from automation import deploy_receipt

    reasons = _roster_reasons(lines)
    assert [(account, found) for account, _, found in reasons] == [
        ("agent", [("roster-required", "-")]),
        ("peer", [("roster-unverified", "-")]),
        ("agent", [("roster-required", "-")]),
    ]
    receipt = _mixed_bundle_receipt(lines)
    items = receipt["held"]
    assert isinstance(items, list)
    held = sorted((item["account"], item["reason"]) for item in items)
    assert held == [("agent", "roster-required"), ("agent", "roster-required"), ("peer", "roster-unverified")]
    pending = deploy_receipt.pending_lines(receipt)
    init_local = [line for line in pending if "init-local" in line]
    assert init_local == [_held_line(2, deploy_receipt._ROSTER_GUIDANCE)]
    assert _held_line(1, deploy_receipt._ROSTER_UNVERIFIED) in pending


def _held_line(count: int, said: str) -> str:
    return f"- 보류 {count}건 — {said}"


def test_a_passing_account_held_by_its_sibling_gets_its_own_reason(tmp_path: Path) -> None:
    """agent 는 검증을 통과(rc 0), peer 는 검증이 돌아 무효(rc 1) — init-local 은 peer 몫뿐이다."""
    from automation import deploy_receipt

    root = _roster_bundle(tmp_path)
    roster = {("agent", "roster"): (0, ""), ("peer", "roster"): (1, "")}
    lines = _collect(root, _scripted(roster, _local_runner(tmp_path / "homes")))
    assert [(account, found) for account, _, found in _roster_reasons(lines)] == [
        ("agent", [("roster-sibling", "-")]),
        ("peer", [("roster-required", "-")]),
        ("agent", [("roster-sibling", "-")]),
    ]
    receipt = _mixed_bundle_receipt(lines)
    items = receipt["held"]
    assert isinstance(items, list)
    held = sorted((item["account"], item["reason"]) for item in items)
    assert held == [("agent", "roster-sibling"), ("agent", "roster-sibling"), ("peer", "roster-required")]
    pending = deploy_receipt.pending_lines(receipt)
    assert [line for line in pending if "init-local" in line] == [_held_line(1, deploy_receipt._ROSTER_GUIDANCE)]
    assert _held_line(2, deploy_receipt._ROSTER_SIBLING) in pending


def test_mixed_roster_failures_keep_each_accounts_own_reason(tmp_path: Path) -> None:
    """agent 는 검증이 돌아 무효(rc 1), peer 는 검증을 못 돌렸다(rc 127) — init-local 은 agent 몫뿐이다."""
    root = _roster_bundle(tmp_path)
    roster = {("agent", "roster"): (1, ""), ("peer", "roster"): (127, "")}
    _assert_mixed_reasons(_collect(root, _scripted(roster, _local_runner(tmp_path / "homes"))))


def test_mixed_roster_failures_through_the_real_remote_script(tmp_path: Path) -> None:
    """실제 REMOTE_SCRIPT 의 roster 분기를 계정마다 다른 PATH 로 돌린다.

    agent 는 검증기를 찾아 실행하고 그 검증기가 1 로 거부한다. peer 는 PATH 에 python3 가
    없어 bash 가 127(명령 없음)을 돌려준다 — 둘 다 진짜 셸 종료코드다.
    """
    root = _roster_bundle(tmp_path)
    homes = tmp_path / "homes"
    rejecting = tmp_path / "agent-bin"
    _write(rejecting / "python3", "#!/bin/sh\nexit 1\n")
    (rejecting / "python3").chmod(0o755)
    bare = tmp_path / "peer-bin"
    bare.mkdir()
    bash = shutil.which("bash")
    assert bash is not None
    (bare / "bash").symlink_to(bash)
    paths = {"agent": f"{rejecting}:{os.environ['PATH']}", "peer": str(bare)}
    rcs: dict[str, int] = {}
    fallback = _local_runner(homes)

    def run(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        if args[0] != "roster":
            return fallback(account, args)
        home = homes / account
        home.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            (bash, "-c", observe.REMOTE_SCRIPT, "_", *args),
            env={"HOME": str(home), "PATH": paths[account]},
            capture_output=True, text=True, check=False, timeout=60,
        )
        rcs[account] = proc.returncode
        return proc.returncode, proc.stdout

    lines = _collect(root, run)
    assert rcs == {"agent": 1, "peer": 127}
    _assert_mixed_reasons(lines)
