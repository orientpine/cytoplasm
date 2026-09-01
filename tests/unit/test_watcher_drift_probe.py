"""계정 홈에 사는 워처 래퍼가 낡으면 헬스체크가 말해야 한다.

2분 리컨실러는 **릴리스 트리만** 수렴시킨다. Hermes no-agent cron 래퍼는 릴리스가 아니라
`~<account>/.hermes/scripts/` 에 살고, 그 계정의 `deploy.sh` 를 사람이 돌려야 갱신된다.
그래서 워처를 고쳐 머지해도 노드는 옛 코드를 계속 돌리는데 — **아무 신호가 없었다**.

스킬 마운트는 `skill_mounts_current` 가, 릴리스 밖 root 자산은 `release_helper_drift` 가
본다. 래퍼만 아무도 보지 않았다. 실제 대가: mailon 런타임 19일 방치, 그리고 2026-08-20
실측에서 `memory_curator_watch`·`memory_relocate_watch` 두 개가 조용히 낡아 있었다.

목적지 이름이 소스 이름과 다를 수 있어(「confirm 워처 파일명은 스킬별로 고유」 규칙 —
calendar 는 `confirm_reaction_watch.py` 를 `calendar_confirm_reaction_watch.py` 로 배포한다)
이름 추론은 성립하지 않는다. 그래서 표가 진실이고, 여기서 그 표를 `deploy.sh` 와 대조한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from automation.watcher_manifest import HOME_DEPLOYED_PATTERN  # noqa: E402
_PROBE: Final = _REPO / "automation" / "watcher_drift_probe.sh"
_MANIFEST: Final = _REPO / "configs" / "watcher-deploy-manifest.txt"
_HEALTHCHECK: Final = _REPO / "automation" / "healthcheck.sh"


def _rows() -> tuple[tuple[str, str, str, str], ...]:
    parsed: list[tuple[str, str, str, str]] = []
    for line in _MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        account, source, destination, policy = line.split("|", 3)
        parsed.append((account, source, destination, policy))
    return tuple(parsed)


def _run_probe(
    tmp_path: Path,
    *,
    deployed: dict[str, str],
    manifest: Path,
    rejected: bool = False,
) -> subprocess.CompletedProcess[str]:
    """`capture_on_node` 를 스텁으로 대체해 ssh 없이 프로브 판정만 돌린다."""
    stub = tmp_path / "stub.sh"
    lines = ["capture_on_node() {", '  local command="$2"']
    for needle, answer in deployed.items():
        lines.append(f'  case "$command" in *{needle}*) printf %s {answer}; return 0 ;; esac')
    # 기본값은 **rc 0 + 빈 출력** — “물어봤고, 거기에 없다”다.
    # `rejected` 는 rc≠ 0 — “물어보지도 못했다”(allowlist 거부·노드 불통).
    lines.extend([f"  return {1 if rejected else 0}", "}"])
    _ = stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return subprocess.run(
        (
            "bash",
            "-c",
            f'source "{stub}"; source "{_PROBE}"; '
            f'probe_watcher_wrappers_current node ops "{manifest}"',
        ),
        capture_output=True,
        text=True,
        check=False,
        # 프로브는 릴리스를 소스로 본다 — 테스트에서는 이 리포가 그 자리다.
        env={**os.environ, "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(_REPO)},
    )


def _manifest(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "manifest.txt"
    _ = path.write_text("# fixture\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# --- the table must describe reality -----------------------------------------------


#: 계정 홈 배포물의 모양은 `automation.watcher_manifest.HOME_DEPLOYED_PATTERN` 이 단일
#: 정의한다(RC-1). 사본이 갈라지며 생긴 사각지대(`scripts/` 만 보던 옛 정규식이 플러그인을
#: 놓쳐 홈 사본이 5일 낡도록 침묵, 2026-08-28 실측)가 이동의 이유다 — 다시 베끼지 말 것.
_HOME_DEPLOYED: Final = HOME_DEPLOYED_PATTERN


def test_every_deploy_script_that_writes_a_wrapper_is_in_the_manifest() -> None:
    """`deploy.sh` 가 새 배포물을 홈에 쓰기 시작하면 이 테스트가 먼저 깨진다."""
    destinations = {destination for _, _, destination, _ in _rows()}
    missing: list[str] = []
    for script in sorted(_REPO.glob("skills/*/deploy.sh")) + sorted(_REPO.glob("automation/*/deploy.sh")):
        for written in _HOME_DEPLOYED.findall(script.read_text(encoding="utf-8")):
            if written not in destinations:
                missing.append(f"{script.relative_to(_REPO)} -> {written}")
    assert not missing, "manifest 에 없는 배포 대상: " + ", ".join(missing)


def test_every_manifest_source_exists_in_the_tree() -> None:
    for _, source, _, _ in _rows():
        assert (_REPO / source).is_file(), f"manifest 가 없는 소스를 가리킨다: {source}"


def test_optional_rows_carry_their_reason() -> None:
    """`optional` 은 탐지를 끄는 스위치다 — 사유 없이는 다음 사람이 결함으로 읽는다."""
    for _, _, destination, policy in _rows():
        if policy.startswith("optional"):
            assert policy.startswith("optional:") and len(policy) > len("optional:"), destination


# --- the probe's judgement ----------------------------------------------------------


def test_a_matching_wrapper_passes(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "agent|automation/healthcheck.sh|.hermes/scripts/w.py|required")
    source_sha = subprocess.run(
        ("sha256sum", str(_REPO / "automation" / "healthcheck.sh")),
        capture_output=True, text=True, check=True,
    ).stdout.split()[0]

    result = _run_probe(tmp_path, deployed={"w.py": source_sha}, manifest=manifest)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WATCHER-DRIFT-PASS" in result.stderr


def test_a_stale_wrapper_fails_and_names_its_deploy_script(tmp_path: Path) -> None:
    """어느 `deploy.sh` 를 돌려야 하는지 함께 말하지 않으면 티켓이 숙제를 넘길 뿐이다."""
    manifest = _manifest(
        tmp_path, "agent|skills/mail/scripts/mail_triage_watch.py|.hermes/scripts/mail_triage_watch.py|required"
    )

    result = _run_probe(tmp_path, deployed={"mail_triage_watch.py": "0" * 64}, manifest=manifest)

    assert result.returncode != 0
    assert "WATCHER-DRIFT" in result.stderr
    assert "skills/mail/deploy.sh" in result.stderr


def test_a_required_wrapper_that_is_absent_fails(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path, "agent|automation/healthcheck.sh|.hermes/scripts/absent.py|required"
    )

    result = _run_probe(tmp_path, deployed={}, manifest=manifest)

    assert result.returncode != 0
    assert "NOT-DEPLOYED" in result.stderr


def test_an_optional_wrapper_that_is_absent_passes(tmp_path: Path) -> None:
    """구독자 전용 컴포넌트는 없는 것이 정상이다 — 상시 red 는 신호가 아니다."""
    manifest = _manifest(
        tmp_path, "agent|automation/healthcheck.sh|.hermes/scripts/opt.py|optional:구독자 전용"
    )

    result = _run_probe(tmp_path, deployed={}, manifest=manifest)

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_optional_wrapper_that_is_deployed_but_stale_still_fails(tmp_path: Path) -> None:
    """optional 은 '없어도 된다'이지 '틀려도 된다'가 아니다."""
    manifest = _manifest(
        tmp_path, "agent|automation/healthcheck.sh|.hermes/scripts/opt.py|optional:구독자 전용"
    )

    result = _run_probe(tmp_path, deployed={"opt.py": "0" * 64}, manifest=manifest)

    assert result.returncode != 0
    assert "WATCHER-DRIFT" in result.stderr

def test_an_unreadable_wrapper_is_unknown_not_missing(tmp_path: Path) -> None:
    """거부를 미배포로 읽으면 손대지 않은 래퍼에 재배포를 지시하게 된다."""
    manifest = _manifest(
        tmp_path, "agent|automation/healthcheck.sh|.hermes/scripts/w.py|required"
    )

    result = _run_probe(tmp_path, deployed={}, manifest=manifest, rejected=True)

    assert result.returncode != 0
    assert "WATCHER-DRIFT-UNKNOWN" in result.stderr
    assert "NOT-DEPLOYED" not in result.stderr, (
        "보지 못한 것을 없다고 단언하면 안 된다 — 그게 프로덕션에서 12행 오탐을 낳았다"
    )


def test_an_optional_wrapper_that_cannot_be_read_is_still_unknown(tmp_path: Path) -> None:
    """optional 은 ‘없어도 된다’일 뿐, 질문이 막힌 것까지 조용히 넘기지는 않는다."""
    manifest = _manifest(
        tmp_path, "agent|automation/healthcheck.sh|.hermes/scripts/opt.py|optional:구독자 전용"
    )

    result = _run_probe(tmp_path, deployed={}, manifest=manifest, rejected=True)

    assert result.returncode != 0
    assert "WATCHER-DRIFT-UNKNOWN" in result.stderr


# --- wiring -------------------------------------------------------------------------


def test_healthcheck_runs_the_probe_remotely_not_locally() -> None:
    """ops 는 agent·peer 로 sudo 할 수 없다 — 로컬 프로브로 두면 영원히 UNKNOWN 이다."""
    text = _HEALTHCHECK.read_text(encoding="utf-8")
    assert "watcher_wrappers_current" in text, "LIVE_CHECKS 와 dispatch 에 배선돼야 한다"
    assert "watcher_drift_probe.sh" in text
    local = next(
        line for line in text.splitlines() if line.startswith("readonly LOCAL_PROBES=")
    )
    assert "watcher_wrappers_current" not in local, (
        "원격 프로브다 — 로컬 목록에 넣으면 cron 계정(ops)의 권한으로 돌아 읽지 못한다"
    )
