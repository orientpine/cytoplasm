"""Stored release v4 envelope replay; staged beside the release spec."""
from __future__ import annotations

from typing import Final, Protocol


class ReleaseView(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def head_sha(self) -> str: ...

    @property
    def release_nonce(self) -> str: ...

    @property
    def surface_digests(self) -> tuple[tuple[str, str], ...]: ...

    @property
    def patch_notes(self) -> str: ...

    @property
    def major_note(self) -> str: ...

    @property
    def detail_message_ids(self) -> tuple[str, ...]: ...

    @property
    def detail_channel_id(self) -> str: ...

    @property
    def detail_guild_id(self) -> str: ...

    def action_hash(self) -> str: ...

    def detail_messages(self) -> tuple[str, ...]: ...


class ReleaseMessageError(ValueError):
    """A release presentation cannot be split or rendered safely."""


def render_v1(spec: ReleaseView, *, approval_line: str) -> str:
    surfaces = "\n".join(
        f"- surface `{name}`: `{digest}`"
        for name, digest in spec.surface_digests
    ) or "- surface: 변경 없음"
    return (
        f"[release] {spec.version} 배포 승인 요청\n"
        f"- version: `{spec.version}`\n"
        f"- HEAD: `{spec.head_sha}`\n"
        f"- release_nonce: `{spec.release_nonce}`\n"
        f"{surfaces}\n"
        "- 패치노트:\n"
        f"{spec.patch_notes.rstrip()}\n"
        f"{approval_line}"
    )


def render_v2(
    spec: ReleaseView, *, bundle_names: str, approval_line: str
) -> str:
    return (
        f"[release] {spec.version} 배포 승인 요청\n"
        f"- 배포 기준: `{spec.head_sha}`\n"
        f"- 배포 번들 ({len(spec.surface_digests)}): {bundle_names}\n"
        f"- 승인 바인딩: `{spec.action_hash()}`\n"
        "- 변경 내용:\n"
        f"{spec.patch_notes.rstrip()}\n"
        f"{approval_line}"
    )


def render_v3(
    spec: ReleaseView, *, bundle_names: str, approval_line: str
) -> str:
    operator = f"{spec.major_note}\n" if spec.major_note else ""
    return (
        f"[release] {spec.version} 배포 승인 요청\n"
        f"- 배포 기준: `{spec.head_sha}`\n"
        f"- 배포 번들 ({len(spec.surface_digests)}): {bundle_names}\n"
        f"- 승인 바인딩: `{spec.action_hash()}`\n"
        f"{operator}"
        f"- 변경 상세: 아래 {len(spec.detail_messages())}개 메시지"
        f" (같은 릴리스 {spec.version} · 기준 {spec.head_sha[:12]})\n"
        f"{approval_line}"
    )

MESSAGE_LIMIT: Final = 1900
DETAIL_BACKLINK_BUDGET: Final = 160


def detail_header(version: str, head_sha: str, index: int, total: int) -> str:
    """Attribute each split detail message to one release and deployment head."""
    return f"[release] {version} 변경 상세 ({index}/{total}) — 기준 {head_sha[:12]}"


def _packed(body: str, budget: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in body.splitlines():
        for piece in [line[at : at + budget] for at in range(0, len(line), budget)] or [""]:
            if current and size + len(piece) + 1 > budget:
                chunks.append("\n".join(current))
                current, size = [piece], len(piece)
                continue
            size += len(piece) + (1 if current else 0)
            current.append(piece)
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def split_messages(
    *, version: str, head_sha: str, body: str, suffix_budget: int = 0
) -> tuple[str, ...]:
    """Split without truncation while keeping every message below Discord's budget."""
    total = 1
    for _ in range(8):
        budget = (
            MESSAGE_LIMIT
            - len(detail_header(version, head_sha, total, total))
            - suffix_budget
            - 1
        )
        chunks = _packed(body, budget)
        if len(chunks) == total:
            return tuple(
                f"{detail_header(version, head_sha, index, total)}\n{chunk}"
                for index, chunk in enumerate(chunks, start=1)
            )
        total = len(chunks)
    raise ReleaseMessageError("release detail messages do not converge on a stable count")


def render_v4(spec: ReleaseView, *, bundle_names: str) -> str:
    """Replay only stored fields, without consulting a clock or release plan."""
    from automation.release_spec import EnvelopeUnavailable

    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError as error:
        raise EnvelopeUnavailable("release owner envelope is unavailable") from error
    here = Ref(scope="self")
    note = f"; {spec.major_note}" if spec.major_note else ""
    # This approval has no render-time expiry: introducing a clock changes the
    # replay bytes and invalidates an already-posted owner's approval.
    try:
        return render(OwnerMessage(
            subject_key=f"release:{spec.version}", subject="릴리스 배포 승인",
            fact=(f"배포 기준 `{spec.head_sha}`;"
                  f" 배포 번들 ({len(spec.surface_digests)}): {bundle_names};"
                  f" 승인 바인딩 `{spec.action_hash()}`;"
                  f" 변경 상세 아래 {len(spec.detail_messages())}개 메시지"
                  f" (같은 릴리스 {spec.version} · 기준 {spec.head_sha[:12]}){note}"),
            location=here,
            owner=Action("react", here, "✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨)"),
            agent_next="승인된 릴리스만 태그·배포", recovery="not_applicable",
            detail=Approval(None, "배포 미실행, 요청 폐기"),
        ), destination=here)
    except OwnerMessageError as error:
        raise EnvelopeUnavailable("release owner envelope cannot render") from error


def render_v5(spec: ReleaseView, *, bundle_names: str) -> str:
    """Readable approval facts, retaining every binding and detail-message reference."""
    from automation.release_spec import EnvelopeUnavailable

    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError as error:
        raise EnvelopeUnavailable("release owner envelope is unavailable") from error
    here = Ref(scope="self")
    facts = [
        f"배포 기준: `{spec.head_sha}`",
        f"배포 묶음 ({len(spec.surface_digests)}): {bundle_names}",
        f"승인 식별값: `{spec.action_hash()}`",
        f"변경 상세: 아래 {len(spec.detail_messages())}개 메시지 "
        + f"(같은 릴리스 {spec.version} · 기준 {spec.head_sha[:12]})",
    ]
    if spec.major_note:
        facts.append(spec.major_note)
    try:
        return render(OwnerMessage(
            subject_key=f"release:{spec.version}", subject=f"릴리스 {spec.version} 배포 승인",
            fact="\n".join(facts), location=here,
            owner=Action("react", here, "✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨)"),
            agent_next="승인된 릴리스만 태그·배포", recovery="not_applicable",
            detail=Approval(None, "배포 미실행, 요청 폐기"), render_version="owner-ko-v2",
        ), destination=here)
    except OwnerMessageError as error:
        raise EnvelopeUnavailable("release owner envelope cannot render") from error


def render_v6(spec: ReleaseView, *, bundle_summary: str) -> str:
    """Compact card with a replayable link to the first posted detail message."""
    from automation.release_spec import EnvelopeUnavailable

    try:
        from automation.interop.owner_message import (
            Action,
            Approval,
            LinkStatus,
            OwnerMessage,
            OwnerMessageError,
            Ref,
            discord_link,
            render,
        )
    except ImportError as error:
        raise EnvelopeUnavailable("release owner envelope is unavailable") from error
    here = Ref(scope="self")
    facts = [
        f"배포 기준: `{spec.head_sha}`",
        f"배포 묶음: {bundle_summary}",
        f"승인 식별값: `{spec.action_hash()}`",
        f"변경 상세: {len(spec.detail_messages())}개 메시지",
    ]
    if spec.major_note:
        facts.append(spec.major_note)
    detail_id = spec.detail_message_ids[0] if spec.detail_message_ids else None
    link = discord_link(
        space="guild" if spec.detail_guild_id else "unknown",
        guild_id=spec.detail_guild_id or None,
        channel_id=spec.detail_channel_id or None,
        message_id=detail_id,
    )
    reference = (
        link.url
        if link.status is LinkStatus.AVAILABLE and link.url is not None
        else "검색: 변경 상세 / "
        f"{spec.version} {spec.head_sha[:12]}"
    )
    try:
        content = render(
            OwnerMessage(
                subject_key=spec.version,
                subject=f"릴리스 {spec.version} 배포 승인",
                fact="\n".join(facts),
                location=here,
                owner=Action(
                    "react",
                    here,
                    "✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨)",
                ),
                agent_next="승인된 릴리스만 태그·배포",
                recovery="not_applicable",
                detail=Approval(None, "배포 미실행, 요청 폐기"),
                render_version="owner-ko-v2",
            ),
            destination=here,
        )
    except OwnerMessageError as error:
        raise EnvelopeUnavailable("release owner envelope cannot render") from error
    body, separator, _footer = content.rpartition("\n")
    if not separator:
        raise EnvelopeUnavailable("release owner envelope has no footer")
    return (
        f"{body}\n-# 참조: 변경 상세 → {reference}"
        f" · {spec.version} · {spec.head_sha[:12]}"
    )
