from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation.interop.approval_lifecycle import ApprovalRequest, Outcome, PostedApproval, Verdict
from automation.memory_curator.binding import entry_digest
from automation.memory_relocate.effects_live import (
    DiscordTransport,
    RelocationStore,
    RelocationStoreError,
    build_effects,
    recover_entry_text,
)
from automation.memory_relocate.model import RelocationRecord, RelocationState, record_key
from automation.memory_relocate.store import load_state, save_state
from automation.memory_relocate.watch_step import ResolveEffects
from automation.obsidian_write.config import ObsidianWriteConfig


def _record(*, entry_text: str, message_id: str | None = None) -> RelocationRecord:
    return RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256=entry_digest("memory", entry_text),
        note_relpath="000_PARA/Resource/relocated.md",
        note_plan_sha256="b" * 64,
        reclaimable_chars=len(entry_text),
        action_hash=f"sha256:{'c' * 64}",
        status="proposed",
        kind="obsidian-write",
        surface="owner-dm",
        channel_id="123456789",
        policy_version=6,
        message_id=message_id,
        created_at="2026-07-31T14:00:00Z",
        approved_at=None,
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=None,
        rag_source_key=None,
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
    )


def _save(path: Path, record: RelocationRecord) -> None:
    save_state(
        path,
        RelocationState(
            version=1,
            relocations={record_key(record.source_kind, record.entry_sha256): record},
        ),
    )


def test_relocation_store_when_message_is_already_bound_refuses_overwrite(tmp_path: Path) -> None:
    # Given: an unbound persisted relocation and its concrete store.
    entry_text = "owner-approved relocation target"
    record = _record(entry_text=entry_text)
    path = tmp_path / "relocations.json"
    _save(path, record)
    store = RelocationStore(path)

    # When: two different message bindings are requested.
    store.set_message_id(record, "message-first", "channel-first")

    # Then: only the first immutable binding survives.
    with pytest.raises(RelocationStoreError):
        store.set_message_id(record, "message-second", "channel-second")
    persisted = load_state(path).relocations[record_key(record.source_kind, record.entry_sha256)]
    assert persisted.message_id == "message-first"


def test_relocation_store_when_exact_binding_is_recommitted_is_idempotent(
    tmp_path: Path,
) -> None:
    # Given: a relocation whose exact message and channel binding already committed.
    entry_text = "owner-approved relocation target"
    record = _record(entry_text=entry_text)
    path = tmp_path / "relocations.json"
    _save(path, record)
    store = RelocationStore(path)
    store.set_message_id(record, "message-first", "channel-first")

    # When: crash recovery recommits the identical four-field binding.
    store.set_message_id(record, "message-first", "channel-first")

    # Then: it succeeds as a no-op and keeps the original record binding.
    persisted = load_state(path).relocations[record_key(record.source_kind, record.entry_sha256)]
    assert persisted.message_id == "message-first"
    assert persisted.channel_id == "channel-first"


def test_relocation_store_when_record_is_updated_roundtrips_replacement(tmp_path: Path) -> None:
    # Given: a bound record awaiting its approved-state timestamp.
    entry_text = "relocation target that remains immutable"
    record = _record(entry_text=entry_text, message_id="message-bound")
    path = tmp_path / "relocations.json"
    _save(path, record)
    store = RelocationStore(path)
    replacement = replace(
        record,
        status="approved",
        approved_at="2026-07-31T14:30:00Z",
    )

    # When: the concrete store persists the replacement.
    store.update(replacement)

    # Then: reloading produces exactly that typed record.
    persisted = load_state(path).relocations[record_key(record.source_kind, record.entry_sha256)]
    assert persisted == replacement


def test_recover_entry_text_when_native_entry_matches_digest_returns_exact_text(tmp_path: Path) -> None:
    # Given: a native memory file with the bound entry and an unrelated neighbor.
    entry_text = "first line\nsecond line"
    other_text = "native entry that must remain"
    _ = (tmp_path / "MEMORY.md").write_text(
        f"{entry_text}\n§\n{other_text}",
        encoding="utf-8",
    )
    record = _record(entry_text=entry_text)

    # When: live recovery searches source-qualified entry digests.
    recovered = recover_entry_text(tmp_path, record)

    # Then: it returns the parser's exact entry text.
    assert recovered == entry_text


def test_recover_entry_text_when_native_digest_is_absent_returns_none(tmp_path: Path) -> None:
    # Given: native memory no longer contains the record's original text.
    _ = (tmp_path / "MEMORY.md").write_text("replacement native entry", encoding="utf-8")
    record = _record(entry_text="removed or edited native entry")

    # When: live recovery searches the current native entries.
    recovered = recover_entry_text(tmp_path, record)

    # Then: no stale content is reconstructed for an external write or delete.
    assert recovered is None



class _FakeResponse:
    status = 200
    reason = "OK"
    headers: dict[str, str] = {}

    def read(self) -> bytes:
        return b'{"id": "chan-1"}'


class _FakeConnection:
    def __init__(self, host: str, timeout: int) -> None:  # noqa: ARG002
        self.requested_path: str | None = None

    def request(self, method: str, path: str, *, body: object, headers: object) -> None:  # noqa: ARG002
        self.requested_path = path

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse()

    def close(self) -> None:
        return None


def test_transport_prefixes_the_discord_api_version_when_requesting(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a captured connection standing in for the Discord REST endpoint.
    captured: dict[str, _FakeConnection] = {}

    def fake_https(host: str, timeout: int) -> _FakeConnection:
        connection = _FakeConnection(host, timeout)
        captured["c"] = connection
        return connection

    monkeypatch.setattr("automation.memory_relocate.effects_live.HTTPSConnection", fake_https)
    transport = DiscordTransport(token="t", owner_id="9")

    # When: any call is issued with an API-relative path.
    _ = transport.api("POST", "/users/@me/channels", {"recipient_id": "9"})

    # Then: the request targets the versioned API, not the discord.com website (HTML would break JSON parse).
    assert captured["c"].requested_path == "/api/v10/users/@me/channels"


@dataclass(frozen=True, slots=True)
class _SequencedResponse:
    status: int
    reason: str
    headers: Message
    body: bytes = b'{"ok": true}'

    def read(self) -> bytes:
        return self.body


class _SequencedConnection:
    def __init__(
        self,
        response: _SequencedResponse,
        requests: list[tuple[str, str]],
    ) -> None:
        self.response = response
        self.requests = requests

    def request(
        self,
        method: str,
        path: str,
        *,
        body: object,
        headers: object,
    ) -> None:
        del body, headers
        self.requests.append((method, path))

    def getresponse(self) -> _SequencedResponse:
        return self.response

    def close(self) -> None:
        return None


def _install_response_sequence(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[_SequencedResponse],
) -> list[tuple[str, str]]:
    requests: list[tuple[str, str]] = []

    def fake_https(host: str, timeout: int) -> _SequencedConnection:
        del host, timeout
        return _SequencedConnection(responses.pop(0), requests)

    monkeypatch.setattr("automation.memory_relocate.effects_live.HTTPSConnection", fake_https)
    return requests


def _headers(retry_after: str | None = None) -> Message:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return headers


def test_transport_when_any_method_is_rate_limited_retries_after_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a DELETE receives one Discord 429 carrying an explicit retry delay.
    requests = _install_response_sequence(
        monkeypatch,
        [
            _SequencedResponse(429, "rate limited", _headers("2.5")),
            _SequencedResponse(200, "OK", _headers()),
        ],
    )
    sleeps: list[float] = []
    transport = DiscordTransport("t", "9", sleeper=sleeps.append, max_attempts=3)

    # When: the method-agnostic API call runs.
    result = transport.api("DELETE", "/channels/c/messages/m")

    # Then: it honors Retry-After and repeats the same method exactly once.
    assert result == {"ok": True}
    assert sleeps == [2.5]
    assert requests == [
        ("DELETE", "/api/v10/channels/c/messages/m"),
        ("DELETE", "/api/v10/channels/c/messages/m"),
    ]


def test_transport_when_rate_limit_reaches_attempt_cap_raises_last_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: every allowed attempt receives a distinct 429 response.
    requests = _install_response_sequence(
        monkeypatch,
        [
            _SequencedResponse(429, "rate limited 1", _headers("0.1")),
            _SequencedResponse(429, "rate limited 2", _headers("0.2")),
            _SequencedResponse(429, "rate limited 3", _headers("0.3")),
        ],
    )
    sleeps: list[float] = []
    transport = DiscordTransport("t", "9", sleeper=sleeps.append, max_attempts=3)

    # When: the explicit attempt limit is exhausted.
    with pytest.raises(HTTPError) as raised:
        _ = transport.api("PUT", "/channels/c/messages/m/reactions/x/@me")

    # Then: the final 429 is preserved and no fourth request or sleep occurs.
    assert raised.value.code == 429
    assert raised.value.reason == "rate limited 3"
    assert sleeps == [0.1, 0.2]
    assert len(requests) == 3


def test_transport_when_server_returns_500_raises_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Discord returns a non-rate-limit server error.
    requests = _install_response_sequence(
        monkeypatch,
        [_SequencedResponse(500, "server error", _headers())],
    )
    sleeps: list[float] = []
    transport = DiscordTransport("t", "9", sleeper=sleeps.append, max_attempts=3)

    # When: the API call observes the response.
    with pytest.raises(HTTPError) as raised:
        _ = transport.api("POST", "/channels/c/messages", {"content": "x"})

    # Then: the original error escapes immediately without retrying.
    assert raised.value.code == 500
    assert sleeps == []
    assert len(requests) == 1


def test_transport_when_429_omits_retry_after_uses_conservative_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Discord rate-limits once without a Retry-After header.
    requests = _install_response_sequence(
        monkeypatch,
        [
            _SequencedResponse(429, "rate limited", _headers()),
            _SequencedResponse(200, "OK", _headers()),
        ],
    )
    sleeps: list[float] = []
    transport = DiscordTransport("t", "9", sleeper=sleeps.append, max_attempts=2)

    # When: the API call retries.
    result = transport.api("GET", "/channels/c/messages/m")

    # Then: the frozen helper's conservative one-second default is used.
    assert result == {"ok": True}
    assert sleeps == [1.0]
    assert len(requests) == 2


def test_relocation_store_when_binding_a_message_persists_its_channel(tmp_path: Path) -> None:
    # Given: a proposed record whose approval surface is not resolved yet (empty channel).
    path = tmp_path / "relocations.json"
    entry_text = "gws example@example.com: calendar, drive, sheets, tasks reference."
    record = replace(_record(entry_text=entry_text), channel_id="")
    _save(path, record)
    store = RelocationStore(path)

    # When: the gate commits the posted approval together with the channel it was posted to.
    store.set_message_id(record, "message-1", "channel-9")

    # Then: BOTH are persisted — the next tick must probe the real surface, never an empty channel
    # (a lost channel binding silently discards the owner's ✅).
    stored = load_state(path).relocations[record_key(record.source_kind, record.entry_sha256)]
    assert stored.message_id == "message-1"
    assert stored.channel_id == "channel-9"


def test_push_approval_record_lets_the_external_effect_gate_accept_the_owner_write(
    tmp_path: Path,
) -> None:
    # Given: the note plan cha approved (the composite hash binds exactly relpath+title+body),
    # and an external-effect gate that has no approval record for the Obsidian push yet.
    from automation.interop.external_effect_gate import ApprovalContext
    from automation.memory_relocate.effects_live import record_push_approval
    from automation.memory_relocate.plan import build_relocation_plan
    from automation.obsidian_write import gate_binding

    plan = build_relocation_plan("gws example: calendar, drive, sheets, tasks reference entry.")
    approval_log = tmp_path / "approvals.jsonl"
    context = ApprovalContext(approval_log=approval_log, owner_id="owner-1", e2e_test_mode=False)
    before = gate_binding.evaluate(plan.note_plan, context=context)
    assert before.allowed is False  # the push is denylisted until the owner approved it

    # When: the owner's ✅ on the relocation approval message is transcribed into the gate's log.
    record_push_approval(
        approval_log,
        action_hash=before.action_hash,
        target_id=before.target_id,
        owner_id="owner-1",
        message_id="message-1",
    )

    # Then: the production gate accepts THIS exact push (and nothing else).
    after = gate_binding.evaluate(plan.note_plan, context=context)
    assert after.allowed is True


@dataclass(frozen=True, slots=True)
class _StubBinding:
    """The one field ``post`` reads off a resolved approval binding."""

    channel_id: str


def _effects_with_verdict(
    verdict: Verdict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> ResolveEffects:
    """Assemble the live effects with every config/network boundary replaced by a stub."""
    module = "automation.memory_relocate.effects_live"

    def fake_load_config() -> ObsidianWriteConfig:
        return ObsidianWriteConfig("git@example.invalid:owner/vault.git", tmp_path / "clone")

    def fake_recover_entry_text(memory_dir: Path, record: RelocationRecord) -> str | None:
        del memory_dir, record
        return "entry"

    def fake_resolve_new_binding(kind: object, directory: object, owner_id: str) -> _StubBinding:
        del kind, directory, owner_id
        return _StubBinding("chan-9")

    def fake_request_approval(
        record: RelocationRecord,
        entry_text: str,
        *,
        store: object,
        transport: object,
        binding: object,
        lease: object,
        journal: object,
    ) -> Verdict:
        del record, entry_text, store, transport, binding, lease, journal
        return verdict

    monkeypatch.setattr(f"{module}.load_config", fake_load_config)
    monkeypatch.setattr(f"{module}.recover_entry_text", fake_recover_entry_text)
    monkeypatch.setattr(f"{module}.resolve_new_binding", fake_resolve_new_binding)
    monkeypatch.setattr(f"{module}.request_approval", fake_request_approval)
    return build_effects(
        memory_dir=tmp_path / "memory",
        state_path=tmp_path / "relocations.json",
        rag_state_path=tmp_path / "rag-state.json",
        token="t",
        owner_id="9",
        now=datetime(2026, 7, 31, 14, 30, tzinfo=UTC),
    )


def test_post_effect_returns_the_message_and_channel_it_posted_to(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a lifecycle verdict that posted the approval to one concrete owner-DM channel.
    record = _record(entry_text="operational reference worth relocating")
    posted = _effects_with_verdict(
        Verdict(Outcome.POSTED, posted=PostedApproval("msg-1", "chan-9")),
        monkeypatch,
        tmp_path,
    )

    # When: the tick asks the live effect for its posting receipt.
    receipt = posted.post_approval(record)

    # Then: BOTH halves of the binding come back — the tick cannot re-derive the channel.
    assert receipt == ("msg-1", "chan-9")

    # And: nothing posted means no binding at all, never a half one.
    unposted = _effects_with_verdict(Verdict(Outcome.PENDING), monkeypatch, tmp_path)
    assert unposted.post_approval(record) is None


def test_post_effect_returns_an_adopted_live_binding_as_a_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: enriched-journal recovery adopted a live message instead of posting a new one.
    record = _record(entry_text="operational reference worth relocating")
    request = ApprovalRequest(
        record_key(record.source_kind, record.entry_sha256),
        record.action_hash,
        "adopted-message",
        "adopted-channel",
        record.created_at,
    )
    effects = _effects_with_verdict(
        Verdict(Outcome.PENDING, live=request),
        monkeypatch,
        tmp_path,
    )

    # When: the watch step asks for the posting receipt.
    receipt = effects.post_approval(record)

    # Then: adoption produces the same complete receipt contract as a fresh POST.
    assert receipt == ("adopted-message", "adopted-channel")


def test_probe_effect_with_an_unbound_channel_is_pending_without_touching_discord(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a corrupted persisted record with a message id but no approval channel.
    record = replace(
        _record(entry_text="operational reference worth relocating", message_id="msg-1"),
        channel_id="",
    )

    class _DiscordMustNotBeTouched:
        def __init__(self, *args: object) -> None:
            del args

        def probe(self, request: object) -> None:
            del request
            raise AssertionError("probe must not touch Discord for an unbound surface")

    monkeypatch.setattr(
        "automation.memory_relocate.effects_live.RelocateApprovalGate",
        _DiscordMustNotBeTouched,
    )
    effects = _effects_with_verdict(Verdict(Outcome.PENDING), monkeypatch, tmp_path)

    # When: the tick probes the persisted approval.
    result = effects.probe_reaction(record)

    # Then: an unbound surface is unverifiable and never reaches Discord.
    assert result == "pending"
