"""Budget result delivery: masked identifiers, fallback coordinates, old runtimes."""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, assert_never

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/budget/scripts"))
if TYPE_CHECKING:
    from skills.budget.scripts import budget_cli, budget_confirm, budget_gate
else:
    import budget_cli
    import budget_confirm
    import budget_gate
from automation.interop import origin_notice, owner_message  # noqa: E402

type DraftValue = str | list[list[str]]
type Draft = dict[str, DraftValue]
type Outcome = Literal["DONE", "CANCELLED", "EXPIRED"]
type ResultOutcome = Literal["executed", "cancelled", "expired"]

# Captured once from the shipped producers with the fixed draft fixture.
LEGACY_SENT = (
    "✉️ 발송 완료: 원장 변경 → office@example.invalid (draft draft-9)\n"
    "소유자 ✅ 승인으로 발송되었습니다."
)
LEGACY_CANCELLED = (
    "⛔ 발송 취소: 원장 변경 → office@example.invalid (draft draft-9)\n"
    "소유자 ⛔ 리액션으로 취소되어 메일은 발송되지 않았습니다."
)


@dataclass(frozen=True, slots=True)
class Chunk:
    message_id: str = "444"


@dataclass(slots=True)
class Wire:
    """Mutable injected transport recording real rendered bodies without network I/O."""
    fail: bool = False
    posts: list[str] = field(default_factory=list)
    fallback: list[str] = field(default_factory=list)
    envelopes: list[owner_message.OwnerMessage] = field(default_factory=list)
    requests: list[tuple[str, str]] = field(default_factory=list)

    def send(self, body: str) -> tuple[Chunk, ...]:
        if self.fail:
            raise OSError("synthetic thread failure")
        self.posts.append(body)
        return (Chunk(),)

    def dm(self, body: str) -> str:
        self.fallback.append(body)
        return "555"

    def api(
        self, method: str, path: str, payload: dict[str, str | bool] | None = None,
    ) -> dict[str, str]:
        self.requests.append((method, path))
        assert path == "/channels/222"
        assert method in {"GET", "PATCH"}
        return {"name": "request"}


@pytest.fixture
def draft() -> Draft:
    return {
        "id": "draft-9", "subject": "원장 변경", "mail_to": "office@example.invalid",
        "approval_guild_id": "111", "approval_thread_id": "222", "message_id": "333",
        "origin_message_id": "666", "status": "pending",
        "changes": [["재료비", "집행액", "1234567", "7654321"]],
        "balance": "9876543", "body": "금액 1234567 잔액 9876543",
    }


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    transport = Wire()
    render = owner_message.render

    def record(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        transport.envelopes.append(message)
        return render(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", record)
    monkeypatch.setattr(budget_confirm, "_api", transport.api)
    monkeypatch.setattr(budget_confirm, "_thread_transport", lambda _channel: transport)
    monkeypatch.setattr(budget_confirm, "dm_owner", transport.dm)
    monkeypatch.setattr(budget_confirm, "_origin_notice", lambda: origin_notice)
    return transport


def legacy_content(draft: Draft, outcome: Outcome) -> str:
    """Capture producer output as delivery input, never as a golden expectation."""
    notices: list[str] = []

    def capture(record: Draft, content: str, *, outcome: str = "") -> str:
        notices.append(content)
        return "444"

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(budget_confirm, "notify_result", capture)
        match outcome:
            case "DONE":
                budget_cli._notify_sent(draft, "manual_reaction")
            case "CANCELLED":
                budget_cli._notify_cancelled(draft)
            case "EXPIRED":
                notices.append("expired-result")
            case unreachable:
                assert_never(unreachable)
    return notices[0]


@pytest.mark.parametrize(("outcome", "expected"), [
    pytest.param("DONE", LEGACY_SENT, id="sent"),
    pytest.param("CANCELLED", LEGACY_CANCELLED, id="cancelled"),
])
@pytest.mark.parametrize("runtime", ["missing-module", "old-signature"])
@pytest.mark.parametrize("fallback", [False, True])
def test_preserves_bytes_when_runtime_predates_envelopes(
    draft: Draft, wire: Wire, monkeypatch: pytest.MonkeyPatch,
    outcome: Outcome, expected: str, runtime: str, fallback: bool,
) -> None:
    # Given: real producer output and either missing module or an old facade signature.
    content = legacy_content(draft, outcome)
    wire.fail = fallback
    if runtime == "missing-module":
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    else:
        def deliver(
            *, api: origin_notice.ApiCall, transport_factory: Callable[[str], Wire],
            record: Draft, thread_name: str, content: str, fallback: Callable[[str], str],
        ) -> str:
            return str(origin_notice.deliver(
                api=api, transport_factory=transport_factory, record=record,
                thread_name=thread_name, content=content, fallback=fallback,
            ))
        monkeypatch.setattr(budget_confirm, "_origin_notice", lambda: SimpleNamespace(deliver=deliver))
    # When: the result is routed through the installed runtime.
    receipt = budget_confirm.notify_result(draft, content, outcome=outcome)
    # Then: independently frozen bytes, receipt and masking survive both surfaces.
    assert content == expected
    assert (wire.fallback if fallback else wire.posts) == [expected]
    assert receipt == ("555" if fallback else "444")
    assert wire.envelopes == []
    assert all(value not in content for value in ("1234567", "7654321", "9876543"))


@pytest.mark.parametrize("outcome", ["DONE", "CANCELLED"])
def test_retains_identifiers_when_delivering_existing_result(
    draft: Draft, wire: Wire, outcome: Outcome,
) -> None:
    # Given: a draft holding amounts and an already-masked producer result.
    content = legacy_content(draft, outcome)
    # When: the result reaches the request thread.
    receipt = budget_confirm.notify_result(draft, content, outcome=outcome)
    # Then: only the existing public identifiers survive, with the last chunk receipt.
    assert receipt == "444"
    assert all(str(draft[key]) in wire.posts[0] for key in ("id", "subject", "mail_to"))
    assert all(value not in wire.posts[0] for value in ("1234567", "7654321", "9876543"))


@pytest.mark.parametrize(("outcome", "expected"), [
    ("DONE", "executed"), ("CANCELLED", "cancelled"), ("EXPIRED", "expired"),
])
@pytest.mark.parametrize("fallback", [False, True])
def test_uses_execution_outcome_when_record_is_still_pending(
    draft: Draft, wire: Wire, outcome: Outcome, expected: ResultOutcome, fallback: bool,
) -> None:
    # Given: the persisted input predates the committed effect; outcome names that effect.
    wire.fail = fallback
    content = legacy_content(draft, outcome)
    # When: reporting the completed effect, not merely the approval.
    budget_confirm.notify_result(draft, content, outcome=outcome)
    # Then: a masked envelope carries execution state and existing factual bytes.
    assert wire.envelopes
    message = wire.envelopes[-1]
    assert message.detail == owner_message.Result(outcome=expected)
    assert message.subject_key == draft["id"]
    assert message.subject == draft["subject"]
    assert message.fact == content
    assert message.owner == owner_message.Action(verb="none")
    assert message.agent_next is None
    assert message.location == owner_message.Ref(
        scope="message", space="guild", guild_id="111", channel_id="222", message_id="333",
        search=("Discord 검색", str(draft["id"])),
    )
    bodies = [repr(message), *wire.posts, *wire.fallback]
    assert all(secret not in body for body in bodies for secret in ("1234567", "7654321", "9876543"))
    assert len((wire.fallback if fallback else wire.posts)[0].splitlines()) == 5


@pytest.mark.parametrize("guild", ["111", ""])
def test_fallback_retains_location_when_thread_transport_fails(
    draft: Draft, wire: Wire, guild: str,
) -> None:
    # Given: a failed request thread, with either known or legacy guild coordinates.
    record = {**draft, "approval_guild_id": guild}
    wire.fail = True
    # When: the shared facade falls back to a different surface.
    receipt = budget_confirm.notify_result(record, legacy_content(draft, "DONE"), outcome="DONE")
    # Then: it re-renders the approval card URL or a search key, never an origin/DM guess.
    assert receipt == "555" and wire.posts == []
    body = wire.fallback[0]
    if guild:
        assert "https://discord.com/channels/111/222/333" in body
    else:
        assert "https://" not in body
        assert wire.envelopes
        assert wire.envelopes[-1].location.search == ("Discord 검색", draft["id"])
    assert all(str(draft[key]) in body for key in ("id", "subject"))
    assert all(secret not in body for secret in ("1234567", "7654321", "9876543"))
    assert wire.requests == []


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("fallback", [False, True])
def test_watch_reports_effect_when_execution_finishes(
    draft: Draft, wire: Wire, monkeypatch: pytest.MonkeyPatch, cancelled: bool, fallback: bool,
) -> None:
    # Given: a real watch command with deterministic approved input and injected effects.
    effects: list[ResultOutcome] = []
    wire.fail = fallback
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(budget_confirm, "owner_id", lambda: "777")
    monkeypatch.setattr(budget_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(budget_confirm, "resolve_reaction", lambda _draft: (
        budget_confirm.CANCEL_EMOJI if cancelled else budget_confirm.APPROVE_EMOJI
    ))
    monkeypatch.setattr(budget_gate, "execute_draft", lambda _draft, _approval: effects.append("executed"))
    monkeypatch.setattr(budget_gate, "discard_draft", lambda _id: effects.append("cancelled"))
    monkeypatch.setattr(budget_cli, "cmd_snapshot", lambda _args: 0)
    monkeypatch.setattr(budget_cli.budget_governed, "refusal", lambda _path: None)
    monkeypatch.setattr(sys, "argv", ["budget_cli.py", "watch"])
    # When: the CLI entry point consumes the decision and reports the committed effect.
    status = budget_cli.main()
    # Then: the real renderer and facade deliver the effect's terminal result, not an ACK.
    assert status == 0
    assert effects == ["cancelled" if cancelled else "executed"]
    assert wire.envelopes
    assert wire.envelopes[-1].detail == owner_message.Result(outcome=effects[0])
    assert (wire.fallback if fallback else wire.posts)


@pytest.mark.parametrize("fallback", [False, True])
def test_preserves_bytes_when_renderer_rejects_envelope(
    draft: Draft, wire: Wire, monkeypatch: pytest.MonkeyPatch, fallback: bool,
) -> None:
    # Given: the optional renderer rejects fields; raw producer content is still safe.
    content = legacy_content(draft, "DONE")
    wire.fail = fallback

    def reject(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        raise owner_message.OwnerMessageError(detail="message.location")

    monkeypatch.setattr(owner_message, "render", reject)
    # When: delivery reaches the renderer's best-effort boundary.
    receipt = budget_confirm.notify_result(draft, content, outcome="DONE")
    # Then: the original masked bytes remain deliverable on either surface.
    assert receipt == ("555" if fallback else "444")
    assert (wire.fallback if fallback else wire.posts) == [content]


def test_retains_raw_ack_when_execution_outcome_is_absent(draft: Draft, wire: Wire) -> None:
    # Given: a nonterminal ACK rather than an executed result.
    content = "ack-sentinel"
    # When: delivery has no terminal marker.
    budget_confirm.notify_result(draft, content)
    # Then: it neither invents completion nor closes the request thread.
    assert wire.posts == [content]
    assert wire.envelopes == [] and wire.requests == []
