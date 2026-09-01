"""Origin-channel thread delivery for owner-approval RESULT notices.

소유자 지시 2026-08-23: 승인 게이트를 가진 모든 스킬은 실행/취소 **결과**를
지시가 시작된 채널의 스레드로 돌려보낸다 — 승인 표면(✅/⛔)은 승인 전용으로
남는다. mail이 첫 구현을 사유화했던 것(triage_confirm)을 이 모듈이 일반화해
스킬별 사본 증식을 막는다(「승인 메시지 단일성 규칙」과 같은 취지).

주입 전용: Discord api 호출자·청킹 전송기·소유자 폴백을 전부 호출자가
넘긴다. 각 스킬의 기존 테스트 monkeypatch 지점(`_api`/`_dm_transport`/
`dm_owner` 류)이 그대로 유효하고, 이 모듈 자체는 토큰·채널 해석을 모른다.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError

ApiCall = Callable[..., object]

#: Discord PUBLIC_THREAD channel type.
_PUBLIC_THREAD = 11
_AUTO_ARCHIVE_MINUTES = 1440
_THREAD_NAME_LIMIT = 100


class _SentChunk(Protocol):
    @property
    def message_id(self) -> str: ...


class _SendsChunks(Protocol):
    def send(self, body: str) -> tuple[_SentChunk, ...]: ...


@dataclass(frozen=True, slots=True)
class OriginRef:
    """Where the owner's instruction came from — empty channel means no origin."""

    channel_id: str
    message_id: str = ""

    @classmethod
    def of_record(cls, record: dict) -> OriginRef:
        return cls(
            channel_id=str(record.get("origin_channel_id") or ""),
            message_id=str(record.get("origin_message_id") or ""),
        )

    def __bool__(self) -> bool:
        return bool(self.channel_id)


def resolve_thread_id(api: ApiCall, origin: OriginRef, name: str) -> str:
    """Anchor on the instruction message when known; 400 means the thread exists."""
    name = name[:_THREAD_NAME_LIMIT]
    if origin.message_id:
        try:
            thread = api(
                "POST",
                f"/channels/{origin.channel_id}/messages/{origin.message_id}/threads",
                {"name": name},
            )
        except HTTPError as error:
            if error.code != 400:
                raise
            return origin.message_id  # 이미 스레드가 달린 메시지 — thread id == message id
        return str(thread["id"])  # type: ignore[index]
    thread = api(
        "POST",
        f"/channels/{origin.channel_id}/threads",
        {"name": name, "type": _PUBLIC_THREAD, "auto_archive_duration": _AUTO_ARCHIVE_MINUTES},
    )
    return str(thread["id"])  # type: ignore[index]


def deliver(
    *,
    api: ApiCall,
    transport_factory: Callable[[str], _SendsChunks],
    record: dict,
    thread_name: str,
    content: str,
    fallback: Callable[[str], object],
) -> object:
    """Post a result notice to the origin thread, else the caller's owner fallback.

    스레드 경로는 best-effort다 — 실패는 NOTIFY-THREAD-FAIL 마커를 남기고
    폴백으로 내려간다(확정된 결과가 반드시 소유자에게 닿아야 하므로). 폴백
    자체의 실패는 삼키지 않는다: 각 스킬의 호출부가 자기 tick 보호 규약대로
    처리한다(mail `_notify_sent` 선례).
    """
    origin = OriginRef.of_record(record)
    if not origin:
        return fallback(content)
    try:
        thread_id = resolve_thread_id(api, origin, thread_name)
        sent = transport_factory(thread_id).send(content)
        return sent[-1].message_id
    except Exception as error:  # noqa: BLE001 — 결과 통지는 표면 실패로 죽지 않는다
        print(
            f"NOTIFY-THREAD-FAIL id={record.get('id', '')} err={type(error).__name__}",
            file=sys.stderr,
        )
        return fallback(content)
