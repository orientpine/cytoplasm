from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_digest  # noqa: E402
from automation.interop import chat_approval_guard  # noqa: E402


def test_digest_header_never_asks_for_a_chat_check_and_passes_the_guard() -> None:
    # Given: a digest with one mail.
    item = {
        "item_no": 1, "uid": "u-1", "subject": "Synthetic", "sender_masked": "h",
        "sensitive": 0, "category": "normal", "flags": (), "summary": "s",
        "note": "", "recv_date": "",
    }
    header = triage_digest.render_digest_parts(
        [item], kst_now=datetime(2026, 10, 8, 8, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )[0]

    # Then: approval is pointed at the draft's own card, and the chat-✅ guard leaves it intact.
    assert "✅" not in header and "승인 카드" in header
    assert chat_approval_guard.guard(header) == header
