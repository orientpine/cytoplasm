"""Approval cards for generic effect requests (owner envelope, append-only versions)."""

from __future__ import annotations

import shlex
from typing import Final

from .model import DRIVE_SAVE, EffectRequest

RENDER_VERSION: Final = "effect-request-v1"
#: Several files or a folder id in one Drive save card (issue #241); v1 card bytes stay frozen.
RENDER_VERSION_BATCH: Final = "effect-request-v2"
MAX_MESSAGE_CHARS: Final = 1900
_COMMAND_LIMIT: Final = 700
_NAME_LIMIT: Final = 60
_SHA_SHOWN: Final = 12


class RenderError(ValueError):
    pass


def version_for(record: EffectRequest) -> str:
    """The card version a record is posted and replayed with: v2 for a batch or folder payload."""
    return RENDER_VERSION_BATCH if record.payload.get("version") == 2 else RENDER_VERSION


def drive_location(payload: dict[str, object]) -> str:
    if payload.get("version") == 2:
        if payload.get("mode") == "folder":
            return "/".join(str(part) for part in payload.get("folder_path", ()))  # type: ignore[union-attr]
        return "/".join(str(part) for part in payload.get("folder_parts", ()))  # type: ignore[union-attr]
    parts = [str(part) for part in payload.get("folder_parts", ())]
    return "/".join((*parts, str(payload.get("drive_name", ""))))


def _size(size: object) -> str:
    value = int(size) if isinstance(size, int) else 0
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def _drive_fact(record: EffectRequest) -> tuple[str, str, str, str]:
    payload = record.payload
    fact = (
        f"할 일: {record.summary}\n"
        f"파일 이름: {payload.get('drive_name')}\n"
        f"Drive 위치: {drive_location(payload)}\n"
        f"공유 범위: 비공개(형 계정만 볼 수 있음) — 다른 사람에게 보이지 않는다\n"
        f"크기: {_size(payload.get('size'))} · sha256: {payload.get('sha256')}\n"
        "같은 이름 파일이 그 폴더에 이미 있으면 그 파일을 새 내용으로 갱신한다"
    )
    return (
        "Drive 저장 승인 요청", fact, "✅ 저장 / ⛔ 취소",
        "✅ 뒤 이 파일을 이 위치에 비공개로 올리고, 다시 내려받아 sha256 이 같은지 확인한 뒤 링크를 남긴다",
    )


def _batch_fact(record: EffectRequest) -> tuple[str, str, str, str]:
    payload = record.payload
    files = [item for item in payload.get("files", ()) if isinstance(item, dict)]  # type: ignore[union-attr]
    total = sum(int(item.get("size", 0)) for item in files)
    lines = []
    for index, item in enumerate(files, start=1):
        name = str(item.get("drive_name"))
        if len(name) > _NAME_LIMIT:
            name = name[: _NAME_LIMIT - 1] + "…"
        lines.append(f"{index}. {name} · {_size(item.get('size'))} · sha256 {str(item.get('sha256'))[:_SHA_SHOWN]}…")
    if payload.get("mode") == "folder":
        place = (f"저장 폴더: {payload.get('folder_name')} (경로 {drive_location(payload)}, "
                 f"id {payload.get('folder_id')})")
    else:
        place = f"Drive 위치: {drive_location(payload)}"
    fact = (
        f"할 일: {record.summary}\n"
        f"파일 {len(files)}개(합계 {_size(total)}) — 이 목록 전체가 승인 해시에 묶인다:\n"
        + "\n".join(lines) + "\n"
        f"{place}\n"
        "공유 범위: 비공개(형 계정만 볼 수 있음) — 다른 사람에게 보이지 않는다\n"
        "같은 이름 파일이 그 폴더에 이미 있으면 그 파일을 새 내용으로 갱신한다"
    )
    return (
        "Drive 저장 승인 요청", fact, "✅ 모두 저장 / ⛔ 취소",
        "✅ 뒤 파일마다 비공개로 올리고 다시 내려받아 sha256 을 확인한다. 일부가 실패하면 나머지는 올리고 "
        "파일별 결과를 이 스레드에 남기며, 실패한 파일만 같은 승인으로 다시 시도한다",
    )


def _command_fact(record: EffectRequest) -> tuple[str, str, str, str]:
    payload = record.payload
    command = shlex.join(tuple(str(item) for item in payload.get("argv", ())))
    if len(command) > _COMMAND_LIMIT:
        command = command[:_COMMAND_LIMIT] + f" …(전체 {len(command)}자, 해시에 전부 묶임)"
    fact = (
        f"할 일: {record.summary}\n"
        f"영향 대상: {payload.get('target')}\n"
        f"실행 명령(작업 폴더 {payload.get('cwd')}):\n```\n{command}\n```\n"
        f"제한 시간: {payload.get('timeout')}초 · 한 번만 실행하고 실패해도 다시 실행하지 않는다"
    )
    return (
        "외부 작업 승인 요청", fact, "✅ 실행 / ⛔ 취소",
        "✅ 뒤 이 명령을 그대로 한 번 실행하고 종료 코드와 출력 끝부분(가린 것)을 이 스레드에 남긴다",
    )


def render_effect_approval(record: EffectRequest, *, render_version: str | None = None) -> str:
    expected = version_for(record)
    if render_version not in (None, expected):
        raise RenderError(f"unsupported effect render version: {render_version}")
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise RenderError("owner envelope unavailable") from error
    if record.effect != DRIVE_SAVE:
        subject, fact, verbs, next_step = _command_fact(record)
    elif expected == RENDER_VERSION_BATCH:
        subject, fact, verbs, next_step = _batch_fact(record)
    else:
        subject, fact, verbs, next_step = _drive_fact(record)
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=record.request_id,
        subject=subject,
        fact=f"{fact}\n판본: {expected}",
        location=here,
        owner=om.Action("react", here, verbs),
        agent_next=next_step,
        recovery="not_applicable",
        detail=om.Approval(None, "아무것도 실행하지 않는다"),
        render_version="owner-ko-v2",
    )
    try:
        lines = om.render(message, destination=here).splitlines()
    except om.OwnerMessageError as error:
        raise RenderError("owner envelope cannot render") from error
    content = "\n".join((*lines[:-1], f"- action_hash: `{record.action_hash}`", lines[-1]))
    if len(content) > MAX_MESSAGE_CHARS:
        raise RenderError("effect approval exceeds the postable length")
    return content
