"""doctor: 등록된 정기 작업이 선언한 필수 환경값(`v2:cron;env=`)이 이 계정에 있는지.

`test_doctor.py` 와 갈라 둔 이유: 그 파일의 픽스처는 실제 저장소 선언을 읽고, 여기서는 선언을
가짜 저장소로 바꿔 끼워야 한다(어떤 작업이 무엇을 요구하는지가 이 검사의 입력이다).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.deploy_declarations import parse_declaration_file
from automation.doctor.capabilities import Finding, evaluate
from automation.doctor.facts import gather
from automation.install.checks import Status
from automation.watcher_manifest import ManifestError

_VALUE = "owner-chosen-" + "7" * 12


def _repo(root: Path, env: str = "X_SOFT_CAP,X_REGION") -> Path:
    manifest = root / "repo" / "automation" / "xreport" / "deploy-manifest.txt"
    manifest.parent.mkdir(parents=True)
    _ = manifest.write_text(
        "agent|automation/xreport/x.py|x-report|"
        f"v2:cron;schedule=0 9 * * *;script=x.py;deliver=local;mode=no-agent;env={env}\n",
        encoding="utf-8",
    )
    return root / "repo"


def _home(root: Path, *, registered: bool = True, env_lines: tuple[str, ...] = ()) -> Path:
    home = root / "home"
    jobs = [{"name": "x-report", "enabled": True, "last_status": "ok"}] if registered else []
    (home / ".hermes" / "cron").mkdir(parents=True)
    _ = (home / ".hermes" / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    _ = (home / ".hermes" / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    return home


def _finding(home: Path, repo: Path) -> Finding:
    facts = gather(account="agent", role="agent", home=home, gateway_unit="hermes-gateway.service",
                   online=False, run=lambda _argv, _extra: (127, ""), uid=1000, repo=repo)
    return next(f for f in evaluate(facts) if f.key == "cron-required-env")


@pytest.fixture(autouse=True)
def _no_inherited_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("X_SOFT_CAP", "X_REGION"):
        monkeypatch.delenv(name, raising=False)


def test_a_registered_job_missing_its_value_is_broken_and_named_without_values(tmp_path: Path) -> None:
    home = _home(tmp_path, env_lines=(f"X_REGION={_VALUE}", "X_SOFT_CAP="))

    finding = _finding(home, _repo(tmp_path))

    assert finding.status is Status.FAIL
    assert finding.subjects == ("X_SOFT_CAP",)
    assert "x-report: X_SOFT_CAP" in finding.detail
    assert _VALUE not in json.dumps([finding.detail, *finding.steps], ensure_ascii=False)
    assert any(".hermes/.env" in step for step in finding.steps)


def test_values_in_the_hermes_env_file_or_process_environment_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("X_REGION", "kr")
    home = _home(tmp_path, env_lines=("X_SOFT_CAP=15",))

    finding = _finding(home, _repo(tmp_path))

    assert (finding.status, finding.detail) == (Status.PASS, "1개 작업의 필수 값 있음")


def test_an_unregistered_job_is_not_checked(tmp_path: Path) -> None:
    finding = _finding(_home(tmp_path, registered=False), _repo(tmp_path))

    assert finding.status is Status.PASS


def test_unreadable_declarations_are_undecided_not_passing(tmp_path: Path) -> None:
    finding = _finding(_home(tmp_path), _repo(tmp_path, env="lower-case"))

    assert finding.status is Status.WARN


@pytest.mark.parametrize("value", ["lower", "A,,B", "A B", "1ABC"])
def test_env_must_list_variable_names(value: str) -> None:
    row = f"agent|automation/x/x.py|job|v2:cron;schedule=* * * * *;script=x.py;deliver=local;mode=no-agent;env={value}\n"

    with pytest.raises(ManifestError):
        _ = parse_declaration_file("automation/x/deploy-manifest.txt", row)


def test_env_is_an_optional_cron_attribute() -> None:
    row = "agent|automation/x/x.py|job|v2:cron;schedule=* * * * *;script=x.py;deliver=local;mode=no-agent;env=A_B,C\n"

    (declaration,) = parse_declaration_file("automation/x/deploy-manifest.txt", row)

    assert declaration.attr("env") == "A_B,C"
