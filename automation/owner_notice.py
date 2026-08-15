"""소유자에게 사건 통지 한 건을 전달한다 — 실패해도 절대 예외를 던지지 않는다.

2026-08-02 실측이 이 모듈의 이유다. 재조정 타이머는 15시간·약 450회 돌면서 한 번도
수렴하지 못했고, 같은 날 healthcheck 는 배포 체크아웃 드리프트를 **52번 FAIL 로 정확히
탐지하고도** 소유자에게 닿지 못했다. 탐지는 여러 겹으로 있는데 도달이 없었다.

두 소비자(재조정 tick · healthcheck 스윕)가 같은 경로를 쓰도록 여기 한 벌만 둔다.
사본이 둘이면 드리프트하고, 깨지는 쪽은 언제나 둘째 사본이다.

자격증명은 새로 만들지 않는다. `/etc/autophagy/repair-approval.env` 가 이미 존재하고
(`root:ops 0640`) 수리 워처가 같은 파일을 쓰며, 두 유닛 모두 `User=ops` 라 읽을 수 있다.
따라서 새 시크릿·새 파일·새 토큰·새 sudoers 가 없다.

**절대 예외를 던지지 않는다**는 것이 가장 중요한 계약이다. 호출자는 False 를 받아야
통지를 큐잉하고 다음 틱에 재시도한다. 예외가 빠져나가면 그 복구가 통째로 무력화되고
호출자의 본래 일(수렴·스윕)까지 함께 죽는다 — 알림을 붙이려다 프로덕션을 멈추는 셈이다.
"""
from __future__ import annotations

import os
import sys


def owner_dm_channel(token: str, owner_id: str) -> str:
    """The DM channel this bot opened with the owner — notices only, never a gate."""
    from automation.interop.approval_directory import DiscordChannelDirectory

    return DiscordChannelDirectory(token=token, owner_id=owner_id).owner_dm()


def send_notice(token: str, channel_id: str, body: str) -> None:
    """The shared sender — it already chunks and honours Discord's Retry-After."""
    from automation.interop.discord_transport import DiscordTransport

    _ = DiscordTransport(token=token, channel_id=channel_id).send(body)


def notify_owner(notice: str) -> bool:
    """Deliver one notice. False means "not delivered" — never an exception.

    The except is broad **on purpose**: a narrower tuple would let one unanticipated
    error stop prod from converging, which is precisely the failure this exists to
    remove. See the module docstring.

    Failure is LOUD. A queued-but-undelivered notice is the same silence the feature
    exists to remove, so every failed attempt leaves a journal line — without the token.
    """
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    owner_id = os.environ.get("AUTOPHAGY_OWNER_ID", "")
    if not token or not owner_id:
        print(
            "[owner-notice] NOTIFY-UNCONFIGURED: owner credential missing, notice not sent",
            file=sys.stderr,
        )
        return False
    try:
        send_notice(token, owner_dm_channel(token, owner_id), notice)
    except Exception as error:  # noqa: BLE001 - see docstring: escaping would stop prod
        print(f"[owner-notice] NOTIFY-FAILED: {type(error).__name__}", file=sys.stderr)
        return False
    return True
