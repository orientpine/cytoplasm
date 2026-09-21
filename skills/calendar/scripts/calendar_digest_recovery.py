"""중단된 다이제스트 카드 게시를 스레드 이력으로 확인해 원장(posting journal·pending)과 맞춘다.

공유 라이프사이클은 '게시 예약만 남고 커밋되지 않은' 키를 fail-closed 로 거부한다
(posting-journal-stale). 단독 초안은 소유자가 수동으로 푸는 것이 맞지만 다이제스트
카드는 무인으로 매 틱 재시도되므로, 그 판정 이전에 여기서 사실을 확인한다: 그 해시의
카드가 스레드에 실제로 올라가 있으면 그것을 살려서(반응 재무장 + pending 커밋) 예약을
지우고, 없으면 예약만 지워 정상 게시로 돌아간다. 이력 조회가 완전히 끝나야만 '없다'고
판정한다 — 조회 실패는 예약을 남긴 채 위로 던져 같은 카드를 두 번 올리지 않는다.
"""
from __future__ import annotations

from urllib.parse import quote

import calendar_confirm
import calendar_gate
from calendar_confirm_input import DraftRecord

_PAGE = 100


def recover(draft: DraftRecord) -> None:
    import calendar_approval

    journal = calendar_approval.posting_journal()
    key = calendar_approval.approval_key(draft)
    with calendar_approval.confirm_lease().hold(key) as owned:
        if not owned:
            raise calendar_gate.GateError("승인 키 잠금 사용 중 — 다음 틱에 재시도", 1)
        reservation = journal.outstanding(key)
        if reservation is None:
            return
        if reservation.get("action_hash") != draft["sha256"]:
            raise calendar_gate.GateError("이전 내용의 승인 게시가 미결 — 수동 확인 필요", 3)
        channel = draft.get("approval_thread_id")
        if not channel:
            raise calendar_gate.GateError("승인 복구 스레드 바인딩 누락", 3)
        message_id = _posted_message_id(channel, f"sha256:{draft['sha256']}")
        if not message_id:
            journal.clear(key)
            return
        for emoji in (calendar_confirm.APPROVE_EMOJI, calendar_confirm.CANCEL_EMOJI):
            calendar_confirm._api("PUT", f"/channels/{channel}/messages/{message_id}/reactions/{quote(emoji, safe='')}/@me")
        facade = calendar_approval.lifecycle()
        gate = calendar_approval.CalendarApprovalGate(draft, calendar_approval.PendingConfirmStore(), calendar_confirm.owner_id())
        gate.commit(
            facade.ApprovalIntent(key, str(draft["sha256"]), channel),
            facade.PostedApproval(message_id, channel), reservation.get("at", ""),
        )
        journal.clear(key)


def _posted_message_id(channel: str, marker: str) -> str:
    """스레드 이력을 끝까지 읽어 그 해시의 카드 id 를 돌려준다. 없으면 빈 문자열."""
    cursor = ""
    while True:
        page = calendar_confirm._api("GET", f"/channels/{channel}/messages?limit={_PAGE}{cursor}")
        if not isinstance(page, list):
            raise calendar_gate.GateError("승인 복구 이력 응답 형식 오류", 3)
        for message in page:
            if not isinstance(message, dict) or not isinstance(message.get("id"), str):
                raise calendar_gate.GateError("승인 복구 이력 메시지 형식 오류", 3)
            if marker in str(message.get("content", "")):
                return message["id"]
        if len(page) < _PAGE:
            return ""
        following = f"&before={page[-1]['id']}"
        if following == cursor:
            raise calendar_gate.GateError("승인 복구 이력 페이지가 진행하지 않음", 3)
        cursor = following
