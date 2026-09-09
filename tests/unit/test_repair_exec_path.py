"""수리 탐지기가 **어느 사본을 실행하는가**를 고정한다.

승계·중복제거 로직을 리포에서 아무리 고쳐도, 게이트웨이와 스킬이 계정 홈의 낡은 사본을
실행하면 그 수정은 프로덕션에 존재하지 않는다. 2026-09-09 실측: 홈 사본이
repair_cli.py 8/12 · repair_core.py 7/16 세대라 2026-09-04 에 들어온 "닫힌 카드의 재발은
새 카드를 연다"가 한 줄도 없었고, 소유자가 올린 수리 요청 2건이 종결된 카드에 묻혀 보드에서
사라졌다. 그 홈 경로는 배포 선언에도 드리프트 프로브 패턴에도 없어 탐지되지 않았다.

이 파일이 검사하는 것은 산문이 아니라 **실행되는 경로 문자열** 하나이고, 그것을 들고 있는 두
사본(파이썬 기본값과 SKILL.md 의 명령)이 갈라지지 않는지다.
"""
from __future__ import annotations

import importlib
import re
import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation.repair import repair_reporter


_REPO: Final = Path(__file__).resolve().parents[2]
_RELEASE_CLI: Final = "/srv/autophagy-agent-current/automation/repair/repair_cli.py"
_HOME_COPY: Final = ".hermes/repair/"


def _reloaded(monkeypatch: pytest.MonkeyPatch, override: str | None):  # type: ignore[no-untyped-def]
    if override is None:
        monkeypatch.delenv("REPAIR_CLI", raising=False)
    else:
        monkeypatch.setenv("REPAIR_CLI", override)
    return importlib.reload(repair_reporter)


def test_the_default_repair_cli_is_the_immutable_release_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reloaded(monkeypatch, None)
    try:
        assert str(module.REPAIR_CLI) == _RELEASE_CLI
    finally:
        _ = _reloaded(monkeypatch, None)


def test_an_explicit_override_still_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """테스트·e2e 가 자기 사본을 선언하는 길은 남는다 — 조용한 폴백만 없앤다."""
    chosen = tmp_path / "repair_cli.py"
    module = _reloaded(monkeypatch, str(chosen))
    try:
        assert module.REPAIR_CLI == chosen
    finally:
        _ = _reloaded(monkeypatch, None)


def test_the_lifecycle_bridge_executes_that_path(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reloaded(monkeypatch, None)
    try:
        seen: list[tuple[str, ...]] = []

        def _capture(argv: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            seen.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(module.subprocess, "run", _capture)
        module.record_lifecycle_failure("task_failed", {"error": "boom", "task_id": "t_x"})

        assert seen and seen[0][2] == _RELEASE_CLI
        assert _HOME_COPY not in " ".join(seen[0])
    finally:
        _ = _reloaded(monkeypatch, None)


def test_the_skill_runbook_runs_the_same_path() -> None:
    """SKILL.md 의 명령 블록은 에이전트가 그대로 실행하는 값이라 파이썬 기본값과 갈라지면 안 된다.

    검사 대상은 산문이 아니라 실행되는 명령이다 — 죽은 홈 경로를 경고로 **언급**하는 문장은
    남을 수 있어야 하고, 그것을 **실행**하는 줄만 없어야 한다.
    """
    runbook = (_REPO / "skills" / "repair" / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", runbook, flags=re.DOTALL)
    assert blocks, "SKILL.md 에 실행 명령 블록이 없다"
    commands = "\n".join(blocks)

    assert _RELEASE_CLI in commands
    assert _HOME_COPY not in commands


def test_the_healthcheck_probe_runs_the_same_path() -> None:
    """헬스체크의 수리 detect 도 같은 사본을 실행해야 한다.

    이 경로가 낡은 사본을 부르는 동안 종결 카드 하나에 재발이 2,829회 쌓였다 — 가장 많이
    도는 detect 경로이므로 여기가 갈라지면 앞의 두 수정이 무의미해진다. 명령은 SSH 강제명령
    allowlist 에 sha256 으로 박히므로, 이 문자열이 바뀌면 소유자가 프로브를 다시 프로비저닝해야
    한다(`automation/provision-healthcheck-probe.sh`).
    """
    manifest = (_REPO / "automation" / "healthcheck_allowlist_manifest.example.txt").read_text(
        encoding="utf-8"
    )

    assert _RELEASE_CLI in manifest
    assert _HOME_COPY not in manifest
