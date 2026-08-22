r"""배포 push 는 착지를 확인해야 한다 — ssh 의 exit 0 은 파일이 갔다는 뜻이 아니다.

2026-08-20 실측: `skills/wiki/deploy.sh` 가 rc=0 으로 끝났는데 노드의 파일은 7월 22일자
그대로였다. 11개 deploy.sh 의 `push_file` 이 전부 바이트 동일했고, 전부
`run_agent "... cat > \$HOME/<dest> ..." < "$source"` 한 줄로 끝났다 — 원격 `cat` 은
stdin 이 비어 있어도 0을 돌려주므로, 아무것도 안 써도 성공으로 보인다. 그 실행에서는
ssh 가 로컬 포워딩 실패를 경고했고(`Could not request local forwarding`), `bash -lc`
로그인 셸이 stdin 을 먼저 소비할 수 있는 구조였다.

`set -euo pipefail` 은 이미 다 붙어 있었다 — 그래서 이건 종료코드 전파 문제가 아니라
**확인하지 않은 쓰기** 문제다. 원격 read-back 해시 대조는 이 리포가 이미 쓰는 방식이다
(obsidian_write 의 push 후 원격 해시 검증, deploy-skill.sh 의 `readlink` 판정).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HELPER = _REPO / "automation" / "deploy_push.sh"
_DEPLOY_SCRIPTS = (
    "automation/managed_sync/deploy.sh",
    "automation/memory_curator/deploy.sh",
    "automation/memory_relocate/deploy.sh",
    "automation/notes_organize/deploy.sh",
    "automation/research_trends/deploy.sh",
    "skills/budget/deploy.sh",
    "skills/calendar/deploy.sh",
    "skills/coordination/deploy.sh",
    "skills/mail/deploy.sh",
    "skills/todo/deploy.sh",
    "skills/wiki/deploy.sh",
)


def _run(tmp_path: Path, *, land: bool, corrupt: bool = False) -> subprocess.CompletedProcess[str]:
    """`run_agent` 를 가짜 노드로 갈아끼워 push→read-back 경로만 돌린다."""
    home = tmp_path / "node-home"
    home.mkdir()
    source = tmp_path / "payload.py"
    _ = source.write_text("print('v2')\n", encoding="utf-8")

    # 가짜 run_agent: 원격 셸 대신 로컬 bash 로 같은 스크립트를 돌린다.
    # land=False 는 stdin 을 버려 "ssh 는 0인데 아무것도 안 쓴" 실측 상황을 만든다.
    stdin_source = '< /dev/null' if not land else ""
    corruptor = "printf 'tampered\\n' > \"$HOME/$1\";" if corrupt else ""
    stub = tmp_path / "stub.sh"
    _ = stub.write_text(
        "run_agent() {\n"
        f'  HOME="{home}" bash -c "$1" {stdin_source}\n'
        "}\n",
        encoding="utf-8",
    )
    script = (
        f'source "{stub}"; source "{_HELPER}"; '
        + (f'( {corruptor} true ) ' if corrupt else "")
        + f'push_file "{source}" "scripts/payload.py"'
    )
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False
    )


def test_a_push_that_lands_succeeds(tmp_path: Path) -> None:
    result = _run(tmp_path, land=True)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_push_that_writes_nothing_is_not_a_success(tmp_path: Path) -> None:
    """실측된 그 상황 — 원격 명령은 0을 냈지만 파일은 그대로였다."""
    result = _run(tmp_path, land=False)

    assert result.returncode != 0, "쓰이지 않았는데 성공으로 보고했다"
    assert "DEPLOY-BLOCK" in result.stderr
    assert "did not land" in result.stderr


def test_the_failure_names_the_destination(tmp_path: Path) -> None:
    """어느 파일이 안 갔는지 말하지 않으면 11개 배포 중 무엇인지 다시 찾아야 한다."""
    result = _run(tmp_path, land=False)

    assert "scripts/payload.py" in result.stderr


# --- 모든 deploy.sh 가 검증된 구현을 쓰는가 -------------------------------------------


def test_every_deploy_script_uses_the_verified_helper() -> None:
    """사본이 11개였기 때문에 이 결함이 11곳에 있었다 — 다시 갈라지지 않게 고정한다."""
    offenders: list[str] = []
    for relative in _DEPLOY_SCRIPTS:
        text = (_REPO / relative).read_text(encoding="utf-8")
        if "deploy_push.sh" not in text or "push_file() {" in text:
            offenders.append(relative)

    assert not offenders, (
        "이 배포 스크립트들이 검증되지 않은 자체 push_file 을 쓴다 — "
        f"automation/deploy_push.sh 를 source 하도록 바꾼다: {offenders}"
    )


def test_the_helper_reads_back_with_its_own_stdin() -> None:
    """read-back 이 호출자의 stdin 을 먹으면 그 다음 push 가 빈 파일을 쓴다."""
    text = _HELPER.read_text(encoding="utf-8")

    assert "/dev/null" in text, "read-back 은 stdin 을 /dev/null 로 막아야 한다"
    assert "sha256sum" in text
