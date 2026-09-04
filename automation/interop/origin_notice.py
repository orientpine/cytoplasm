"""Origin-channel thread delivery for owner-approval RESULT notices.

소유자 지시 2026-08-23: 승인 게이트를 가진 모든 스킬은 실행/취소 **결과**를
지시가 시작된 채널의 스레드로 돌려보낸다 — 승인 표면(✅/⛔)은 승인 전용으로
남는다. mail이 첫 구현을 사유화했던 것(triage_confirm)을 이 모듈이 일반화해
스킬별 사본 증식을 막는다(「승인 메시지 단일성 규칙」과 같은 취지).

주입 전용: Discord api 호출자·청킹 전송기·소유자 폴백을 전부 호출자가
넘긴다. 각 스킬의 기존 테스트 monkeypatch 지점(`_api`/`_dm_transport`/
`dm_owner` 류)이 그대로 유효하고, 이 모듈 자체는 토큰·채널 해석을 모른다.

소유자 결정 2026-09-01: 승인 요청이 요청별 스레드(`approval_surface.RequestThread`)
에 게시되므로 결과도 **그 스레드**로 간다 — 레코드의 `approval_thread_id` 가 있으면
스레드를 새로 열지 않고 거기에 게시한 뒤, 종결 결과(`ThreadOutcome`)면 이름에 상태
접두어를 붙이고 아카이브한다. 그래서 승인 채널의 활성 스레드 목록이 곧 진행 중
요청 목록이 된다. 이름 변경·아카이브는 best-effort 다(`THREAD-CLOSE-FAIL`).
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
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


class ThreadOutcome(StrEnum):
    """Terminal status words prefixed to the request thread name on close."""

    DONE = "✅ 완료"
    CANCELLED = "⛔ 취소"
    EXPIRED = "⌛ 만료"


_STATUS_PREFIXES = tuple(f"{outcome.value} · " for outcome in ThreadOutcome)


@dataclass(frozen=True, slots=True)
class OriginRef:
    """Where the result goes — the approval thread first, else the instruction origin.

    ``thread_id`` is the per-request approval thread (record ``approval_thread_id``);
    when set no thread is created. Empty everything means no origin (owner fallback).
    """

    channel_id: str
    message_id: str = ""
    thread_id: str = ""

    @classmethod
    def of_record(cls, record: dict) -> OriginRef:
        return cls(
            channel_id=str(record.get("origin_channel_id") or ""),
            message_id=str(record.get("origin_message_id") or ""),
            thread_id=str(record.get("approval_thread_id") or ""),
        )

    def __bool__(self) -> bool:
        return bool(self.thread_id or self.channel_id)


def resolve_thread_id(api: ApiCall, origin: OriginRef, name: str) -> str:
    """The approval thread as-is; else anchor on the instruction message (400 = exists)."""
    if origin.thread_id:
        return origin.thread_id
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


def close_thread(
    api: ApiCall,
    thread_id: str,
    outcome: ThreadOutcome,
    *,
    record_id: str = "",
) -> bool:
    """Rename the request thread with its status prefix and archive it (best-effort).

    An earlier prefix is replaced, never stacked. Any failure is a THREAD-CLOSE-FAIL
    marker and ``False`` — the notice already landed and nothing downstream may change.
    """
    try:
        current = api("GET", f"/channels/{thread_id}")
        name = str(current.get("name") or "") if isinstance(current, dict) else ""
        for prefix in _STATUS_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        payload: dict[str, object] = {"archived": True}
        if name:
            payload["name"] = f"{outcome.value} · {name}"[:_THREAD_NAME_LIMIT]
        api("PATCH", f"/channels/{thread_id}", payload)
    except Exception as error:  # noqa: BLE001 — 종결 표시는 결과 통지를 깨지 않는다
        print(
            f"THREAD-CLOSE-FAIL id={record_id} thread={thread_id} err={type(error).__name__}",
            file=sys.stderr,
        )
        return False
    return True


def deliver(
    *,
    api: ApiCall,
    transport_factory: Callable[[str], _SendsChunks],
    record: dict,
    thread_name: str,
    content: str,
    fallback: Callable[[str], object],
    outcome: ThreadOutcome | None = None,
) -> object:
    """Post a result notice to the request/origin thread, else the owner fallback.

    스레드 경로는 best-effort다 — 실패는 NOTIFY-THREAD-FAIL 마커를 남기고
    폴백으로 내려간다(확정된 결과가 반드시 소유자에게 닿아야 하므로). 폴백
    자체의 실패는 삼키지 않는다: 각 스킬의 호출부가 자기 tick 보호 규약대로
    처리한다(mail `_notify_sent` 선례). ``outcome`` 이 있으면 스레드 게시가
    성공한 뒤에만 그 스레드를 종결 표시한다 — 폴백 경로에는 닫을 스레드가 없다.
    """
    origin = OriginRef.of_record(record)
    if not origin:
        return fallback(content)
    try:
        thread_id = resolve_thread_id(api, origin, thread_name)
        sent = transport_factory(thread_id).send(content)
    except Exception as error:  # noqa: BLE001 — 결과 통지는 표면 실패로 죽지 않는다
        print(
            f"NOTIFY-THREAD-FAIL id={record.get('id', '')} err={type(error).__name__}",
            file=sys.stderr,
        )
        return fallback(content)
    if outcome is not None:
        close_thread(api, thread_id, outcome, record_id=str(record.get("id", "")))
    return sent[-1].message_id
