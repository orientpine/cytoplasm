"""정기 작업이 시작하자마자 죽지 않도록 — 선언된 필수 환경값이 이 계정에 있는지 본다.

cron 스크립트가 기본값 없이 요구하는 값은 배포 선언의 `v2:cron;…;env=<이름>[,…]` 이 정한다
(`automation/deploy_declarations.py`, 강제는 `tests/unit/test_cron_required_env.py`). Hermes 는
no-agent 작업 직전에 `~/.hermes/.env` 를 다시 읽어 그 값을 스크립트에 넘기므로, 값이 있어야 할
곳은 그 파일(또는 이 프로세스의 환경)이다. 값은 읽어도 들고 나오지 않는다 — 이름만 돌려준다.

2026-09-04~10-02: 일일 지출 보고가 필수 경보 기준(`COST_REPORT_SOFT_CAP`) 없이 배포돼 29일 동안
매일 `KeyError` 로 죽었고, doctor 는 이미 여러 번 실패한 뒤에야 「반복 실패」로 알았다.
이 점검은 등록된 작업이 처음 돌기 전에 같은 결함을 이름으로 짚는다.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Final, TypeAlias

from automation.deploy_declarations import all_declarations
from automation.install.checks import Status
from automation.watcher_manifest import ManifestError

Gap: TypeAlias = tuple[str, tuple[str, ...]]
Observed: TypeAlias = "tuple[tuple[Gap, ...], int] | str | None"
HERMES_ENV: Final = Path(".hermes") / ".env"


def required_env(repo: Path, role: str) -> dict[str, tuple[str, ...]]:
    return {
        declaration.destination: tuple(declaration.attr("env").split(","))
        for declaration in all_declarations(repo)
        if declaration.kind == "cron" and declaration.account == role and declaration.attr("env")
    }


def _names(values: Mapping[str, str] | None) -> frozenset[str]:
    return frozenset(key for key, value in (values or {}).items() if value)


def observe(
    *, repo: Path, role: str, registered: Collection[str],
    hermes_env: Mapping[str, str] | None, environ: Mapping[str, str],
) -> tuple[tuple[Gap, ...], int] | str:
    """등록된 작업마다 빠진 필수 값. 반환은 (빠진 것, 점검한 작업 수) 또는 판정 불가 사유.

    등록되지 않은 작업은 보지 않는다 — 그 노드가 쓰지 않는 선택 작업에 값을 요구하면 거짓 경보다.
    """
    try:
        wanted = required_env(repo, role)
    except (ManifestError, OSError) as error:
        return f"배포 선언을 읽지 못했다({type(error).__name__})"
    present = _names(hermes_env) | _names(environ)
    checked = {job: keys for job, keys in wanted.items() if job in registered}
    gaps = tuple(
        (job, missing)
        for job, keys in sorted(checked.items())
        if (missing := tuple(key for key in keys if key not in present))
    )
    return gaps, len(checked)


def judge(observed: Observed) -> tuple[Status, str, tuple[str, ...]]:
    match observed:
        case None:
            return Status.WARN, "필수 설정값을 관측하지 않았다", ()
        case str() as reason:
            return Status.WARN, f"{reason} — 정기 작업의 필수 값을 판정하지 못했다", ()
        case ((), 0):
            return Status.PASS, "필수 값을 선언한 등록 작업이 없다", ()
        case ((), checked):
            return Status.PASS, f"{checked}개 작업의 필수 값 있음", ()
        case (gaps, _):
            detail = "; ".join(f"{job}: {', '.join(keys)}" for job, keys in gaps)
            names = tuple(sorted({key for _, keys in gaps for key in keys}))
            return Status.FAIL, f"필수 값이 없다 — {detail} (다음 실행이 시작하자마자 실패한다)", names
