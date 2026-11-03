"""Q474: one state mark, before the kind icon, on every naming transition."""
from __future__ import annotations

import pytest

from automation.interop import digest_thread, origin_notice
from automation.interop.approval_surface import ApprovalKind, RequestThread, request_thread_name


@pytest.mark.parametrize("name,base", [
    ("✅ ❗ 실패 · 🔐 sample", "🔐 sample"),
    ("⛔ 취소 · 🔄 ✉️ sample", "✉️ sample"),
    ("✅ ⛔ 취소 · ↩️ sample", "↩️ sample"),
    ("🔄 완료 · 📄 sample", "📄 sample"),
    ("⏸️ ⏸ ⌛ 만료 · 💤 🔔 📌 📬 sample", "📬 sample"),
])
@pytest.mark.parametrize("operation", ["close", "resume", "rename"])
def test_all_renames_remove_every_old_state(name: str, base: str, operation: str) -> None:
    calls = []

    def api(method, path, payload=None):
        calls.append((method, path, payload))
        return {"name": name}

    if operation == "close":
        assert origin_notice.close_thread(api, "123", origin_notice.ThreadOutcome.DONE)
        expected = f"✅ {base}"
    elif operation == "resume":
        assert origin_notice.resume_thread(api, "123")
        expected = f"🔔 {base}"
    else:
        assert origin_notice.rename_thread(api, "123", name)
        expected = f"{name.split()[0]} {base}"
        if name.startswith("⏸"):
            expected = f"🔔 {base}"
    assert calls[-1][2]["name"] == expected


@pytest.mark.parametrize("outcome,mark,archived", [
    ("DONE", "✅", True), ("CANCELLED", "⛔", True), ("EXPIRED", "💤", True),
    ("FAILED", "❗", False),
])
def test_outcome_is_bare_and_failure_stays_open(outcome, mark, archived) -> None:
    calls = []
    assert origin_notice.close_thread(
        lambda *args: calls.append(args), "123", origin_notice.ThreadOutcome[outcome],
        base_name="🔔 ✉️ sample",
    )
    assert calls == [("PATCH", "/channels/123", {"name": f"{mark} ✉️ sample", "archived": archived})]


def test_mail_subject_is_visible_immediately_after_state_and_kind() -> None:
    assert request_thread_name(
        ApprovalKind.MAIL_REPLY, RequestThread("RE: fWd: FW: sample meeting"),
    ) == "🔔 ↩️ sample meeting"


def test_legacy_mail_name_loses_redundant_label_and_reply_chain_on_rename() -> None:
    assert origin_notice.state_thread_name(
        "✅ 완료 · ↩️ 메일 회신 · RE: FW: Fwd: sample meeting", "🔔",
    ) == "🔔 ↩️ sample meeting"


@pytest.mark.parametrize("pending,mark", [(1, "🔔"), (0, "✅")])
def test_today_digest_tracks_its_cards(pending, mark) -> None:
    thread = digest_thread.DigestThread("2026-11-03", "123", "agent-chat-thread", 8, None,
                                      "🔔 📬 sample digest")
    calls, saved = [], []
    digest_thread.settle(
        {"123": pending}, lambda *args: calls.append(args),
        store=digest_thread.DigestStore((thread,), saved.append),
    )
    assert calls[-1][2]["name"] == f"{mark} 📬 sample digest"
    assert saved[0].mark == ("pending" if pending else "settled")
