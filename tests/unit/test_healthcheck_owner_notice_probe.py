"""설치가 넘긴 결정을 아무도 되묻지 않던 자리.

설치기는 `DISCORD_BOT_TOKEN` 이 환경에 없으면 `discord-readiness` 를 WARN 으로 남기고
「소유자 확인 절차」를 출력한다(2026-09-09). 그런데 **그 절차를 돌렸는지 되묻는 것이
없었다** — `automation/healthcheck_registry.sh` 의 어느 행도 소유자 통지 자격증명을 보지
않으므로, 끝내 채우지 않은 노드는 수렴 실패도 드리프트도 소유자에게 알리지 못하면서
`ALL_HEALTHY` 를 계속 보고할 수 있다.

**네트워크를 부르지 않는다.** 매 틱 Discord API 를 부르면 Discord 장애가 수리 티켓이 되고,
레이트리밋·주기라는 정책 질문이 새로 생긴다. 조용한 실패의 실제 모양은 "끝내 안 채웠다"
이고 그것은 `/etc/autophagy/repair-approval.env`(설치기가 빈 템플릿으로 놓는 파일)만 읽으면
판정된다. 선례는 `peer_gateway_probe.sh` — 로컬·읽기 전용·설정 드리프트·fail-closed다.

**빈 파일은 정당한 구성이다.** `docs/troubleshooting/신규-노드-설치-공백.md` §2-1 이
"빈 파일로 두어도 된다"고 안내했고 그 말은 여전히 옳다. 그래서 이 프로브가 묻는 것은
「채웠는가」가 아니라 **「결정했는가」**다 — 자격증명을 채우거나, `OWNER_NOTICE_OPTIONAL=1`
로 통지를 받지 않겠다고 선언하거나. 둘 다 아닌 상태만이 아무도 내리지 않은 결정이다.

프로브가 증명하지 **못하는 것**: 토큰의 유효성·Message Content 인텐트·채널 접근·DM 도달.
게이트웨이가 쓰는 토큰은 agent 홈(0600)에 있어 ops 프로브가 읽을 수 없다. 그 절반은
소유자가 `automation/install/discord_check.py` 를 한 번 돌려 닫는다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_PROBE: Final = _REPO / "automation/owner_notice_probe.sh"
_HEALTHCHECK: Final = _REPO / "automation/healthcheck.sh"

# 실제 토큰 모양이 아닌 고정 문자열 — 이 값이 출력에 나타나는지가 기준 2의 판정이다.
_SECRET: Final = "fixture-value-Q7bLm2-not-a-real-credential"
_OWNER_ID: Final = "246813579"

_FILLED: Final = f"DISCORD_BOT_TOKEN={_SECRET}\nAUTOPHAGY_OWNER_ID={_OWNER_ID}\n"
_TEMPLATE: Final = (
    "# Owner-notice credentials, read by automation/owner_notice.py.\n"
    "# DISCORD_BOT_TOKEN=\n"
    "# AUTOPHAGY_OWNER_ID=\n"
    "# OWNER_NOTICE_CHANNEL_ID=\n"
)


def _probe(
    path: Path,
    script: str = 'source "$1"; probe_owner_notice_credentials primary ops "$3"',
) -> subprocess.CompletedProcess[str]:
    """실제 bash 로 소스해 실행한다 — 프로덕션이 스윕에서 하는 것과 같은 방식."""
    return subprocess.run(
        ["bash", "-c", script, "bash", str(_PROBE), str(_HEALTHCHECK), str(path)],
        env={
            **os.environ,
            "HEALTHCHECK_NODE_CONFIG_PATH": str(_REPO / "configs/node.example.toml"),
            "HEALTHCHECK_OWNER_NOTICE_CREDENTIAL": str(path),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "repair-approval.env"
    _ = path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 아무도 내리지 않은 결정은 조용하지 않다.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "content",
    [
        _TEMPLATE,
        "",
        "DISCORD_BOT_TOKEN=\nAUTOPHAGY_OWNER_ID=\n",
        f"DISCORD_BOT_TOKEN={_SECRET}\n",
        f"AUTOPHAGY_OWNER_ID={_OWNER_ID}\n",
        # systemd EnvironmentFile 은 나중 줄이 이긴다 — 지웠다가 다시 못 채운 상태.
        f"DISCORD_BOT_TOKEN={_SECRET}\nAUTOPHAGY_OWNER_ID={_OWNER_ID}\nDISCORD_BOT_TOKEN=\n",
        f'DISCORD_BOT_TOKEN=""\nAUTOPHAGY_OWNER_ID={_OWNER_ID}\n',
    ],
)
def test_a_missing_decision_fails_the_sweep_rather_than_passing_quietly(
    tmp_path: Path, content: str,
) -> None:
    # Given: 설치기가 놓은 빈 템플릿, 또는 절반만 채운 파일.
    path = _write(tmp_path, content)

    # When: 스윕이 그 노드를 본다.
    result = _probe(path)

    # Then: 통지가 도달할 수 없다는 사실이 보인다. 이 실패가 없으면 그 노드는 수렴 실패도
    # 드리프트도 소유자에게 알리지 못하면서 ALL_HEALTHY 를 보고한다.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "OWNER-NOTICE-CREDENTIAL-MISSING" in result.stdout
    assert "OWNER-NOTICE-CREDENTIAL-RECOVERY:" in result.stdout
    assert "OWNER-NOTICE-CREDENTIAL-PASS" not in result.stdout


def test_a_configured_credential_passes(tmp_path: Path) -> None:
    # Given: 소유자가 두 값을 채운 노드.
    path = _write(tmp_path, _FILLED)

    # When
    result = _probe(path)

    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNER-NOTICE-CREDENTIAL-PASS" in result.stdout


@pytest.mark.parametrize(
    "content",
    [
        f"{_TEMPLATE}OWNER_NOTICE_OPTIONAL=1\n",
        "OWNER_NOTICE_OPTIONAL=yes\n",
    ],
)
def test_an_explicit_opt_out_is_a_configured_decision_and_passes(
    tmp_path: Path, content: str,
) -> None:
    # Given: 통지를 받지 않겠다고 **선언한** 노드. 빈 파일로 두는 것은
    # docs/troubleshooting/신규-노드-설치-공백.md §2-1 이 허용한 구성이고 그 말은 여전히
    # 옳다 — 이 프로브가 닫는 것은 "결정하지 않음"이지 "받지 않음"이 아니다.
    path = _write(tmp_path, content)

    # When
    result = _probe(path)

    # Then: 통과하되 어느 쪽으로 통과했는지 로그가 말한다. 두 통과를 같은 문구로 내면
    # 소유자가 선언한 적 없는 노드와 구별할 수 없다.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNER-NOTICE-CREDENTIAL-DECLARED-OPTIONAL" in result.stdout


# --------------------------------------------------------------------------- #
# 비밀은 존재만 보고한다.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("content", [_FILLED, f"DISCORD_BOT_TOKEN={_SECRET}\n"])
def test_the_secret_value_never_reaches_the_output(tmp_path: Path, content: str) -> None:
    # Given: 실제 값이 들어 있는 자격증명 파일(통과 경로와 실패 경로 양쪽).
    path = _write(tmp_path, content)

    # When
    result = _probe(path)

    # Then: 프로브 출력은 수리 티켓 본문이 되고 로그에 남는다 — 키 **이름**은 말하되
    # 값은 어디에도 나오지 않아야 한다. 프로브가 **실제로 그 파일을 읽고 판정했는지**를
    # 먼저 못박는다: 아무것도 실행되지 않아도 "값이 없다"는 참이므로, 그 단언만 두면
    # 프로브가 사라진 날에도 통과한다.
    assert "OWNER-NOTICE-CREDENTIAL-" in result.stdout, result.stdout + result.stderr
    assert _SECRET not in result.stdout + result.stderr


# --------------------------------------------------------------------------- #
# 판정할 수 없으면 통과가 아니다.
# --------------------------------------------------------------------------- #
def test_the_recovery_names_the_file_it_actually_judged(tmp_path: Path) -> None:
    # Given: 카탈로그 행이 넘긴 경로와 모듈 기본값이 다른 실행. 프로덕션에서는 둘이 같은
    # 식에서 오지만, 경로의 출처가 둘이면 언젠가 갈라진다 — 그리고 갈라진 순간 운영자는
    # 판정된 적 없는 파일을 고치게 된다.
    judged = tmp_path / "judged.env"
    _ = judged.write_text(_TEMPLATE, encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.env"

    # When
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; probe_owner_notice_credentials primary ops "$2"',
            "bash",
            str(_PROBE),
            str(judged),
        ],
        env={**os.environ, "HEALTHCHECK_OWNER_NOTICE_CREDENTIAL": str(elsewhere)},
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    # Then: 안내는 읽은 파일을 가리킨다.
    assert result.returncode == 1, result.stdout + result.stderr
    assert str(judged) in result.stdout, result.stdout
    assert str(elsewhere) not in result.stdout, result.stdout


def test_an_absent_file_is_unreadable_so_the_sweep_fails_closed(tmp_path: Path) -> None:
    # Given: 재조정 유닛이 `-` 접두 없이 요구하는 파일이 아예 없는 노드.
    path = tmp_path / "repair-approval.env"

    # When
    result = _probe(path)

    # Then: 부재는 "통지가 필요 없다"가 아니라 설치가 덜 끝났다는 뜻이다.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "OWNER-NOTICE-CREDENTIAL-ABSENT" in result.stdout
    assert "OWNER-NOTICE-CREDENTIAL-RECOVERY:" in result.stdout


def test_a_non_regular_file_is_unreadable_so_the_sweep_fails_closed(tmp_path: Path) -> None:
    # Given: 파일 자리에 디렉터리 — 테스트가 root 로 돌아도 정규 파일이 아니다.
    path = tmp_path / "repair-approval.env"
    path.mkdir()

    # When
    result = _probe(path)

    # Then: 모르는 상태는 실패다. 읽지 못한 것을 통과로 접으면 이 프로브가 존재하는 이유가
    # 사라진다.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "OWNER-NOTICE-CREDENTIAL-UNREADABLE" in result.stdout


# --------------------------------------------------------------------------- #
# 프로덕션 배선.
# --------------------------------------------------------------------------- #
def test_dispatches_locally_when_registered(tmp_path: Path) -> None:
    # Given: 채워진 자격증명과 실제 healthcheck 정의.
    path = _write(tmp_path, _FILLED)
    script = """source "$2"
for definition in "${LIVE_CHECKS[@]}"; do
  IFS='|' read -r name kind node account target <<< "$definition"
  if [[ "$kind" == owner_notice_credentials ]]; then
    [[ " $LOCAL_PROBES " == *" $kind "* ]] || exit 2
    run_check "$definition"
    exit $?
  fi
done
exit 3
"""

    # When: 프로덕션 디스패처가 이 프로브를 고른다.
    result = _probe(path, script)

    # Then: 카탈로그에 있고, ssh 를 타지 않는 목록에 있고, 실제로 불린다. LOCAL_PROBES 에
    # 없으면 fleet 전체 SSH 장애 때 all-remote-down 가드가 무너진다(회귀 d7ed0ad/γ).
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNER-NOTICE-CREDENTIAL-PASS" in result.stdout
