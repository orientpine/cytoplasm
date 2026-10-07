"""Agent replies never ask the owner to ✅ a chat message (2026-10-07 regression).

The reply that caused the incident is reproduced verbatim in shape: it asked for a ✅
on itself and carried no approval-card link. Replies that cite a card pass unchanged.
"""

from __future__ import annotations

import pytest

from automation.interop.chat_approval_guard import NOTICE, guard

_INCIDENT = (
    "요청하신 경로와 내용을 그대로 담은 저장 초안을 준비했습니다.\n"
    "대상: `000_PARA/Resource/x.md`\n"
    "**소유자께서 이 메시지에 ✅로 승인해 주십시오.** ⛔는 취소입니다."
)


@pytest.mark.parametrize(
    "reply",
    [
        _INCIDENT,
        "초안입니다. ✅ 눌러 주시면 저장하겠습니다.",
        "Please react ✅ to approve.",
    ],
)
def test_chat_check_requests_without_a_card_link_are_replaced(reply: str) -> None:
    guarded = guard(reply)
    assert guarded.endswith(NOTICE)
    assert "✅로 승인해 주십시오" not in guarded and "눌러 주시면" not in guarded


@pytest.mark.parametrize(
    "reply",
    [
        "승인 스레드에 게시했습니다: https://discord.com/channels/1/2 — 카드에 ✅ 해 주세요.",
        "APPROVAL-THREAD request=abc url=unavailable search=abc\n카드에서 ✅ 승인해 주세요.",
        "✅ Obsidian 노트 저장 완료: 000_PARA/x.md",
        "일정을 조회했습니다. 등록은 하지 않았습니다.",
    ],
)
def test_card_links_receipts_and_plain_replies_pass_unchanged(reply: str) -> None:
    assert guard(reply) == reply
