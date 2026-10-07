"""`healthcheck_delegated.sh` — 릴리스 시점에 위임 표면의 상시 프로브만 다시 돌린다.

진입 파일을 source 하고 기존 디스패처 `run_check` 로 세 종류만 실행하므로 새 원격 명령이
없다(허용 목록 불변). 가짜 진입 파일이 `LIVE_CHECKS` 와 호출을 기록하는 `run_check` 를
정의하고, 마지막 시험만 실제 `healthcheck.sh` 를 source 해 sweep 이 시작되지 않음을 본다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_SCRIPT: Final = _REPO / "automation" / "healthcheck_delegated.sh"
_ENTRY: Final = """set -euo pipefail
LIVE_CHECKS=(
  "c1 helpers|release_helper_drift|node1|ops|t1"
  "c2 unit|user_unit_active|node1|agent|u"
  "c3 wrapper|healthcheck_wrapper_current|node1|ops|t3"
  "c4 rag|rag_stack_current|rag1|ops|t4"
  "c5 trust|update_trust|node1|ops|t5"
)
run_check() {
  local name probe node
  IFS='|' read -r name probe node _ <<< "$1"
  printf '%s\\n' "$probe" >> "$RECORD"
  case "${FAKE_MODE:-pass}:$probe" in
    fail:release_helper_drift)
      echo "[release-helper] HELPER-DRIFT: autophagy-install-release differs from release source" >&2
      echo "[release-helper] re-run the provisioner on the node: sudo bash <release>/automation/provision-release-store.sh" >&2
      return 1 ;;
    unknown:rag_stack_current)
      echo "[runtime-package] RUNTIME-PACKAGE-UNKNOWN: cannot read ops@rag1:personal-rag — run automation/rag_stack/deploy.sh then activate MCP" >&2
      return 1 ;;
    *) echo "[probe] PASS $probe" ; return 0 ;;
  esac
}
"""


def _run(tmp_path: Path, mode: str = "pass") -> tuple[subprocess.CompletedProcess[str], list[str]]:
    entry = tmp_path / "entry.sh"
    _ = entry.write_text(_ENTRY, encoding="utf-8")
    record = tmp_path / "record.txt"
    proc = subprocess.run(
        ("bash", str(_SCRIPT)),
        env={
            "HOME": str(tmp_path),
            "PATH": os.environ["PATH"],
            "HEALTHCHECK_DELEGATED_ENTRY": str(entry),
            "RECORD": str(record),
            "FAKE_MODE": mode,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    called = record.read_text(encoding="utf-8").split() if record.exists() else []
    return proc, called


def _rows(stdout: str, kind: str) -> list[list[str]]:
    return [line.split("|", 4) for line in stdout.splitlines() if line.split("|", 1)[0] == kind]


def test_only_delegated_probe_types_are_run(tmp_path: Path) -> None:
    proc, called = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert called == ["release_helper_drift", "healthcheck_wrapper_current", "rag_stack_current"]
    checks = _rows(proc.stdout, "DELEGATED")
    assert [(row[1], row[2], row[3], row[4]) for row in checks] == [
        ("release_helper_drift", "node1", "PASS", "c1 helpers"),
        ("healthcheck_wrapper_current", "node1", "PASS", "c3 wrapper"),
        ("rag_stack_current", "rag1", "PASS", "c4 rag"),
    ]
    assert proc.stdout.splitlines()[-1] == "DELEGATED-END|3"
    kinds = {line.split("|", 1)[0] for line in proc.stdout.splitlines()}
    assert kinds == {"DELEGATED", "DELEGATED-DETAIL", "DELEGATED-END"}


def test_a_failing_probe_is_reported_with_its_own_lines(tmp_path: Path) -> None:
    proc, _ = _run(tmp_path, "fail")
    assert proc.returncode == 0, proc.stderr
    statuses = {row[1]: row[3] for row in _rows(proc.stdout, "DELEGATED")}
    assert statuses["release_helper_drift"] == "FAIL"
    details = [row[3] for row in _rows(proc.stdout, "DELEGATED-DETAIL") if row[1] == "release_helper_drift"]
    assert details == [
        "[release-helper] HELPER-DRIFT: autophagy-install-release differs from release source",
        "[release-helper] re-run the provisioner on the node: sudo bash <release>/automation/provision-release-store.sh",
    ]


def test_an_unknown_marker_is_reported_as_unknown(tmp_path: Path) -> None:
    proc, _ = _run(tmp_path, "unknown")
    assert proc.returncode == 0, proc.stderr
    statuses = {row[1]: row[3] for row in _rows(proc.stdout, "DELEGATED")}
    assert statuses == {
        "release_helper_drift": "PASS",
        "healthcheck_wrapper_current": "PASS",
        "rag_stack_current": "UNKNOWN",
    }


def test_a_broken_entry_fails_the_script(tmp_path: Path) -> None:
    entry = tmp_path / "entry.sh"
    _ = entry.write_text("set -euo pipefail\nfalse\n", encoding="utf-8")
    proc = subprocess.run(
        ("bash", str(_SCRIPT)),
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"],
             "HEALTHCHECK_DELEGATED_ENTRY": str(entry)},
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert proc.returncode != 0
    assert "DELEGATED-END" not in proc.stdout


def test_sourcing_the_real_entry_does_not_start_a_sweep(tmp_path: Path) -> None:
    private = tmp_path / "private"
    hermes = tmp_path / ".hermes"
    hermes.mkdir()
    _ = (hermes / "node.toml").write_text(f'private_root = "{private}"\n', encoding="utf-8")
    proc = subprocess.run(
        (
            "bash", "-c",
            'source "$1" >/dev/null; [[ "$(type -t run_check)" == function ]] && '
            'printf "SOURCED %s\\n" "${#LIVE_CHECKS[@]}"',
            "_", str(_REPO / "automation" / "healthcheck.sh"),
        ),
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("SOURCED ")
    assert not private.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == [".hermes"]
