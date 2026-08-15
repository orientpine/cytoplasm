"""Deterministic metadata-only classification surface for the mail wrapper."""

from __future__ import annotations

CLASSIFY_RULES = {
    "spam": ("(광고)", "[광고]", "(ad)", "[ad]", "unsubscribe", "수신거부", "광고메일"),
    "patent_sensitive": ("특허", "출원", "patent", "지재권", "ip출원", "발명신고"),
    "budget": ("과제비", "예산", "정산", "영수증", "집행", "연구비", "구매요청"),
    "schedule": ("회의", "미팅", "일정", "세미나", "발표", "초청", "초대", "meeting"),
    "reply_needed": ("회신", "답장", "요청", "문의", "확인 부탁", "제출", "기한", "마감"),
}
NOTICE_SENDER_MARKERS = ("noreply", "no-reply", "notification", "안내", "공지")


def classify_metadata(subject: str, sender: str) -> dict:
    """Classify only subject/sender metadata; bodies and LLMs never enter here."""
    text = f"{subject} {sender}".lower()
    flags = {name: any(marker in text for marker in markers) for name, markers in CLASSIFY_RULES.items()}
    if flags["spam"]:
        category = "spam"
    elif flags["patent_sensitive"] or flags["budget"] or flags["reply_needed"] or flags["schedule"]:
        category = "important"
    elif any(marker in sender.lower() for marker in NOTICE_SENDER_MARKERS):
        category = "notice"
    else:
        category = "general"
    return {
        "category": category,
        "flags": {key: value for key, value in flags.items() if key != "spam"},
        "route": "non-glm" if flags["patent_sensitive"] else "glm-ok",
        "basis": "metadata-only",
    }
