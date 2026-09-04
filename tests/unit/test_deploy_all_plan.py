"""RC-3/4: 배포 전량 판정(deploy_all)·관측(deploy_all_probe)·영수증의 단위 테스트.

이 판정이 막는 실패 모드: 부분 배포가 성공으로 보고되는 것(C3). 그래서 여기의 축은
"조금이라도 어긋나면 clean 이 아니다"와 "보지 못한 것은 깨끗한 것이 아니다"(fail-closed),
그리고 "영수증은 clean 에만 서명된다"이다.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from automation import deploy_all  # noqa: E402
from automation.deploy_all import (  # noqa: E402
    ObservationError,
    parse_observations,
    render_actions,
    render_plan,
    render_receipt,
)
from automation.deploy_all_probe import observations  # noqa: E402
from automation.deploy_all_probe import _read_home  # noqa: E402
from automation.skill_review import skill_digest  # noqa: E402


def _obs(*extra: str) -> list[str]:
    return [
        "OBS|release|abc123",
        "OBS|mounts|judged",
        "OBS|home|agent|.hermes/scripts/w.py|skills/mail/scripts/w.py|required|aaa|aaa",
        *extra,
        "OBS|end",
    ]


def test_clean_observations_make_a_clean_plan() -> None:
    plan = parse_observations(_obs())
    assert plan.clean
    assert render_actions(plan) == ""
    assert "전량 일치" in render_plan(plan)


def test_stale_home_row_names_its_deployer() -> None:
    plan = parse_observations(
        _obs("OBS|home|agent|.hermes/scripts/x.py|skills/mail/scripts/x.py|required|aaa|bbb")
    )
    assert not plan.clean
    assert plan.packages_to_deploy == ("skills/mail",)
    assert "ACT|run-deployer|skills/mail/deploy.sh" in render_actions(plan)


def test_absent_required_is_a_defect_but_absent_optional_is_not() -> None:
    required = parse_observations(
        _obs("OBS|home|agent|.hermes/scripts/x.py|skills/mail/scripts/x.py|required|aaa|")
    )
    optional = parse_observations(
        _obs("OBS|home|agent|.hermes/scripts/y.py|automation/managed_sync/cron/y.py|optional|aaa|")
    )
    assert not required.clean
    assert optional.clean


def test_optional_but_stale_still_fails() -> None:
    """optional 은 '없어도 된다'이지 '틀려도 된다'가 아니다 — watcher 프로브와 같은 의미."""
    plan = parse_observations(
        _obs("OBS|home|agent|.hermes/scripts/y.py|automation/managed_sync/cron/y.py|optional|aaa|bbb")
    )
    assert not plan.clean


def test_unreadable_home_is_fail_closed_and_not_deployable() -> None:
    plan = parse_observations(
        _obs("OBS|home|agent|.hermes/scripts/x.py|skills/mail/scripts/x.py|required|aaa|?")
    )
    assert not plan.clean
    assert plan.packages_to_deploy == ()  # 배포기로 고칠 수 있는 문제가 아니다
    assert "ACT|manual|unreadable:agent:.hermes/scripts/x.py" in render_actions(plan)


def test_stale_mount_plans_a_skill_deploy() -> None:
    plan = parse_observations(_obs("OBS|mount-stale|meeting|aaa|bbb"))
    assert plan.skills_to_deploy == ("meeting",)
    assert "ACT|deploy-skill|meeting" in render_actions(plan)


def test_orphaned_mount_is_manual_not_automated() -> None:
    plan = parse_observations(_obs("OBS|mount-orphaned|ghost"))
    assert not plan.clean
    assert plan.skills_to_deploy == ()
    assert "ACT|manual|orphaned-mount:ghost" in render_actions(plan)


def test_stale_plugin_row_requires_a_gateway_restart() -> None:
    plan = parse_observations(
        _obs(
            "OBS|home|agent|.hermes/plugins/00-meeting-gate/__init__.py"
            "|skills/meeting/plugin/__init__.py|required|aaa|bbb"
        )
    )
    assert plan.gateway_restart_needed
    assert "ACT|restart-gateway|agent+peer" in render_actions(plan)


def test_truncated_observations_are_refused() -> None:
    with pytest.raises(ObservationError):
        _ = parse_observations(
            ["OBS|release|abc", "OBS|mounts|judged", "OBS|home|a|b|c|required|x|x"]
        )  # OBS|end 없음


def test_missing_mount_judgement_is_refused() -> None:
    with pytest.raises(ObservationError):
        _ = parse_observations(["OBS|release|abc", "OBS|home|a|b|c|required|x|x", "OBS|end"])


def test_receipt_refuses_a_dirty_plan() -> None:
    plan = parse_observations(_obs("OBS|mount-stale|meeting|aaa|bbb"))
    with pytest.raises(ObservationError):
        _ = render_receipt(plan, verified_at="2026-08-29T00:00:00+00:00")


def test_receipt_records_the_release_sha_and_its_boundary() -> None:
    plan = parse_observations(_obs())
    receipt = json.loads(render_receipt(plan, verified_at="2026-08-29T00:00:00+00:00"))
    assert receipt["release_sha"] == "abc123"
    assert receipt["version"] == deploy_all.RECEIPT_VERSION
    assert receipt["delegated"]  # ⑤⑥ 위임 경계가 영수증에 명시된다


def _runtime_fixture(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    (runtime / "skills" / "demo").mkdir(parents=True)
    _ = (runtime / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\n---\n", encoding="utf-8"
    )
    (runtime / "automation" / "pkg").mkdir(parents=True)
    _ = (runtime / "automation" / "pkg" / "w.py").write_text("print()\n", encoding="utf-8")
    (runtime / "configs").mkdir()
    _ = (runtime / "configs" / "watcher-deploy-manifest.txt").write_text(
        "agent|automation/pkg/w.py|.hermes/scripts/w.py|required\n", encoding="utf-8"
    )
    live = tmp_path / "live"
    live.mkdir()
    digest = skill_digest(runtime / "skills" / "demo")
    (live / "demo").symlink_to(tmp_path / "releases" / "demo" / digest)
    return runtime, live


def test_probe_observations_round_trip_clean(tmp_path: Path) -> None:
    """관측 → 판정이 실제 파일·마운트 모양으로 왕복한다(노드 없이, reader 주입)."""
    runtime, live = _runtime_fixture(tmp_path)
    source_sha = hashlib.sha256(
        (runtime / "automation" / "pkg" / "w.py").read_bytes()
    ).hexdigest()
    lines = observations(
        runtime, live, lambda _account, _dest: source_sha, lambda _account: ()
    )
    plan = parse_observations(lines)
    assert plan.clean
    assert plan.release_sha == "runtime"


def test_probe_reports_a_stale_wrapper(tmp_path: Path) -> None:
    runtime, live = _runtime_fixture(tmp_path)
    lines = observations(
        runtime, live, lambda _account, _dest: "0" * 64, lambda _account: ()
    )
    plan = parse_observations(lines)
    assert not plan.clean
    assert plan.packages_to_deploy == ("automation/pkg",)


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    ((3, "", ""), (1, "", "?"), (0, "a" * 64 + "\n", "a" * 64)),
)
def test_home_reader_distinguishes_absent_from_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    expected: str,
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess((), returncode, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _read_home("agent", ".hermes/scripts/w.py") == expected
