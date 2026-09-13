"""Mail result delivery compatibility and destination regressions."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
from automation.interop import origin_notice  # noqa: E402
from automation.interop.discord_transport import SentMessage  # noqa: E402


@dataclass(frozen=True, slots=True)
class Delivery:
    """Injected delivery capture; only the contained logs accumulate calls."""
    fail: bool = False
    posts: list[str] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)

    def send(self, content: str) -> tuple[SentMessage, ...]:
        self.posts.append(content)
        if self.fail:
            raise HTTPError("https://discord.invalid", 503, "unavailable", None, None)
        return (SentMessage(message_id="444"),)

    def fallback(self, content: str) -> str:
        self.fallbacks.append(content)
        return "555"


@pytest.fixture
def delivery(monkeypatch: pytest.MonkeyPatch) -> Delivery:
    capture = Delivery()
    monkeypatch.setattr(triage_confirm, "_origin_notice", lambda: origin_notice)
    monkeypatch.setattr(triage_confirm, "_api", lambda *_args: {"name": "mail"})
    monkeypatch.setattr(triage_confirm, "_dm_transport", lambda _channel: capture)
    monkeypatch.setattr(triage_confirm, "dm_owner", capture.fallback)
    return capture


@pytest.fixture
def draft() -> dict[str, str]:
    return {
        "id": "draft-fixture", "subject": "fixture-subject", "to": "to@example.invalid",
        "body": "private-body-sentinel", "quote": "private-quote-sentinel",
        "cc": "cc@example.invalid", "approval_guild_id": "111",
        "approval_thread_id": "222", "message_id": "333", "status": "pending",
    }


@pytest.mark.parametrize("method", ["sent", "cancelled"])
def test_legacy_bytes_when_runtime_predates_envelopes(
    monkeypatch: pytest.MonkeyPatch, draft: dict[str, str], delivery: Delivery, method: str,
) -> None:
    # Given: an old facade accepting only today's string arguments.
    def legacy(*, api, transport_factory, record, thread_name, content, fallback, outcome=None):
        return origin_notice.deliver(
            api=api, transport_factory=transport_factory, record=record, thread_name=thread_name,
            content=content, fallback=fallback, outcome=outcome,
        )
    monkeypatch.setattr(triage_confirm, "_origin_notice", lambda: SimpleNamespace(
        deliver=legacy, ThreadOutcome=origin_notice.ThreadOutcome,
    ))
    expected = (
        f"✉️ 발송 완료: {draft['subject']} → {draft['to']} (draft {draft['id']})\n"
        "소유자 ✅ 승인으로 발송되었습니다."
        if method == "sent" else
        f"⛔ 발송 취소: {draft['subject']} → {draft['to']} (draft {draft['id']})\n"
        "소유자 ⛔ 리액션으로 취소되어 메일은 발송되지 않았습니다."
    )
    # When: the real result producer reports its already-committed effect.
    if method == "sent":
        triage_cli._notify_sent(draft, "manual_reaction")
    else:
        triage_cli._notify_cancelled(draft)
    # Then: shipped legacy copy is byte-identical, including recipient visibility.
    assert delivery.posts == [expected]
    assert delivery.fallbacks == []


def test_raw_content_when_result_is_not_terminal(draft: dict[str, str], delivery: Delivery) -> None:
    # Given: an intermediate notice, not a committed effect.
    content = "intermediate-sentinel\n  unchanged"
    # When: no terminal outcome is supplied.
    result = triage_confirm.notify_result(draft, content)
    # Then: it is not relabeled as executed and bytes are untouched.
    assert (result, delivery.posts) == ("444", [content])


def test_legacy_bytes_when_envelope_module_is_missing(
    monkeypatch: pytest.MonkeyPatch, draft: dict[str, str], delivery: Delivery,
) -> None:
    # Given: the optional module is absent, even though the facade is available.
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    content = "result-sentinel\n  unchanged"
    # When: a committed result is delivered.
    result = triage_confirm.notify_result(draft, content, outcome="DONE")
    # Then: importing the optional contract cannot prevent legacy delivery.
    assert (result, delivery.posts) == ("444", [content])


def test_envelope_when_sent_result_lands_in_its_thread(
    draft: dict[str, str], delivery: Delivery, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real renderer captures the envelope without replacing delivery.
    from automation.interop import owner_message
    messages: list[owner_message.OwnerMessage] = []
    render = owner_message.render

    def capture(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        messages.append(message)
        return render(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", capture)
    # When: the committed send producer reports success (record may still be pending).
    triage_cli._notify_sent(draft, "manual_reaction")
    # Then: terminal semantics and existing visibility are encoded, with no self-link.
    assert len(messages) == 1
    message = messages[0]
    assert (message.subject_key, message.subject) == (draft["id"], draft["subject"])
    assert (message.detail, message.recovery) == (owner_message.Result("executed"), "irreversible")
    assert message.owner == owner_message.Action("none") and message.agent_next is None
    assert message.location == owner_message.Ref(
        scope="message", space="guild", guild_id="111", channel_id="222", message_id="333",
        search=("Discord 검색", draft["id"]),
    )
    [body] = delivery.posts
    assert len(body.splitlines()) == 5
    assert "discord.com/channels" not in body
    assert draft["to"] in body
    assert all(draft[key] not in body for key in ("body", "quote", "cc"))


@pytest.mark.parametrize("guild", [True, False])
def test_r4_rerenders_when_thread_post_fails(
    monkeypatch: pytest.MonkeyPatch, draft: dict[str, str], guild: bool,
) -> None:
    # Given: an HTTP failure after thread-local rendering, with a different fallback surface.
    capture = Delivery(fail=True)
    monkeypatch.setattr(triage_confirm, "_origin_notice", lambda: origin_notice)
    monkeypatch.setattr(triage_confirm, "_dm_transport", lambda _channel: capture)
    monkeypatch.setattr(triage_confirm, "dm_owner", capture.fallback)
    if not guild:
        del draft["approval_guild_id"]
    # When: the actual producer reports the committed mail send.
    triage_cli._notify_sent(draft, "manual_reaction")
    # Then: the fallback is independently rendered, preserving card coordinates or search.
    [body] = capture.fallbacks
    assert body != capture.posts[0]
    if guild:
        assert body.count("https://discord.com/channels/111/222/333") == 1
    else:
        assert "discord.com" not in body and "@me" not in body
        assert draft["id"] in body.splitlines()[2]
    assert "이 스레드" not in body
    assert all(draft[key] not in body for key in ("body", "quote", "cc"))


@pytest.mark.parametrize(("outcome", "expected"), [("CANCELLED", "cancelled"), ("EXPIRED", "expired")])
def test_recovery_when_no_mail_was_sent(
    monkeypatch: pytest.MonkeyPatch, delivery: Delivery, outcome: str, expected: str,
) -> None:
    # Given: cancellation or expiry, with optional approval coordinates absent.
    from automation.interop import owner_message
    messages: list[owner_message.OwnerMessage] = []
    render = owner_message.render

    def capture(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        messages.append(message)
        return render(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", capture)
    # When: a terminal result is delivered without an origin.
    triage_confirm.notify_result({"id": "draft-fixture", "subject": "fixture"}, "fact", outcome=outcome)
    # Then: no execution or irreversible send is claimed, and the search survives missing ids.
    assert len(messages) == 1
    assert messages[0].detail.outcome == expected
    assert messages[0].recovery == "not_applicable"
    [body] = delivery.fallbacks
    assert "discord.com" not in body and "@me" not in body
    assert "draft-fixture" in body.splitlines()[2]
