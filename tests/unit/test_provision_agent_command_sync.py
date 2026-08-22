"""게이트웨이 slash-command sync 정책 drop-in — 부팅당 요청 1회(bulk)를 고정한다.

WHY: Hermes 0.20.3의 기본 safe reconcile은 커맨드 diff를 건당 mutation으로 보낸다.
2026-08-18 업스트림 업데이트처럼 커맨드 전부가 한꺼번에 달라지면 Discord의 작은
command 버킷 안에서 한 번에 끝나지 못해 429로 끊기고, 성공 기록이 남지 않아 매
부팅 재시도한다. 배포 재시동이 잦은 날(8/20 16회·8/21 23회)은 그 루프가 앱 전체를
429 패널티 창에 가둬 승인 요청의 auto-thread 생성까지 함께 죽었다(2026-08-22
01:32 KST 사건). ``DISCORD_COMMAND_SYNC_POLICY=bulk`` 는 diff 크기와 무관하게
부팅당 PUT 1회라 그 실패 계열 전체에 면역이다.

여기서 고정하는 것은 산문이 아니라 배포 산출물이다 — drop-in 파일의 정확한
바이트(systemd 가 소비)와 프로비저너의 배선(``ensure_file_if_absent`` 경유,
only-if-unset: 이미 값을 바꿔 둔 노드는 보존된다).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "automation" / "provision-agent.sh"


def _function_body(name: str) -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}", text, re.S | re.M)
    assert match is not None, f"provision-agent.sh must define {name}()"
    return match.group(1)


def test_render_command_sync_dropin_emits_bulk_policy() -> None:
    """drop-in 내용은 systemd가 그대로 소비한다 — 실제 bash로 렌더해 바이트를 고정."""
    body = _function_body("render_command_sync_dropin")
    rendered = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert rendered == "[Service]\nEnvironment=DISCORD_COMMAND_SYNC_POLICY=bulk\n"


def test_command_sync_dropin_is_wired_only_if_unset() -> None:
    """새 drop-in은 기존 10-env-secrets 와 같은 only-if-unset 경로로 배선되어야 한다.

    ensure_file_if_absent 를 거치지 않으면 노드에서 소유자가 값을 바꿨을 때
    재프로비저닝이 그 결정을 덮어쓴다(추적 config = 불변 시드 규칙 위반).
    """
    text = _SCRIPT.read_text(encoding="utf-8")
    assert 'DROPIN_SYNC_FILE="$DROPIN_DIR/30-command-sync.conf"' in text
    assert 'DROPIN_SYNC_FILE=""' in text

    body = _function_body("ensure_initial_config_and_dropin")
    assert re.search(r'render_command_sync_dropin > "\$desired_sync_dropin"', body)
    assert (
        'ensure_file_if_absent "$desired_sync_dropin" "$DROPIN_SYNC_FILE" '
        '"$DROPIN_DIR" "systemd command-sync drop-in"' in body
    )
