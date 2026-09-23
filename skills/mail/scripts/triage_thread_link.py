"""승인 스레드 좌표 한 줄 — 에이전트 답장이 스레드를 인용할 수 있게 CLI stdout 에 싣는다."""
from __future__ import annotations

from collections.abc import Mapping


def _thread_url(draft: Mapping[str, object]) -> str | None:
    guild, thread = draft.get("approval_guild_id"), draft.get("approval_thread_id")
    if not (isinstance(guild, str) and guild and isinstance(thread, str) and thread):
        return None
    try:
        from automation.interop.owner_message import LinkStatus, discord_link
    except ImportError:
        return None
    link = discord_link(space="guild", channel_id=thread, guild_id=guild)
    return link.url if link.status is LinkStatus.AVAILABLE else None


def approval_thread_line(draft: Mapping[str, object]) -> str | None:
    """게시된 초안만 줄을 낸다. 좌표를 모르면 링크를 지어내지 않고 draft id 를 검색 키로 준다."""
    if not draft.get("message_id"):
        return None
    draft_id = draft["id"]
    url = _thread_url(draft)
    target = f"url={url}" if url else f"url=unavailable search={draft_id}"
    return f"APPROVAL-THREAD draft={draft_id} {target}"
