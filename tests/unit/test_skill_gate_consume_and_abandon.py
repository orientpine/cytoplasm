"""Retiring a skill gate's pending record: consume-on-mount, and the audited abandon.

Nothing ever retired a record whose decision the MOUNT stage had already consumed, so
the lifecycle guard (L3 — an owner-decided request is never destroyed) turned that
debris into ``outcome=deferred reason=owner-decided`` on the next deploy of the skill.

Locks both halves: ``consume`` compare-and-swaps on the RAW ``(skill, hash, message_id)``
that was mounted — benign when the record moved on, never fatal, never Discord — and
``abandon`` refuses on ANY field mismatch, fsyncs its audit line BEFORE the record
disappears, and leaves the owner's message up. The Discord surface is an in-memory fake
with an ordered call log — no mocks, so "never deletes" is asserted on what the gate did.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

from automation import (
    skill_gate,
    skill_gate_request,
    skill_gate_retire,
    skill_gate_specs,
    skill_gate_surface,
)
from automation.interop.approval_surface import ChannelFacts

_DIGEST = "a" * 64
_OTHER_DIGEST = "c" * 64
_OWNER_ID = "111111111111111111"
_CHANNEL_ID = "100000000000000009"
_SKILL = "wiki"
_REASON = "record predates consume-on-mount; artifact already live"
_ACTOR = "cha"
_NOT_FOUND = HTTPError("https://discord.test", 404, "error", Message(), None)



class FakeDiscord:
    """In-memory #approvals channel: real message store plus an ordered call log."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.contents: dict[str, str] = {}
        self.reactions: dict[tuple[str, str], list[str]] = {}
        self.posted = 0

    def api(self, method: str, path: str, payload: dict[str, str] | None = None) -> object:
        self.calls.append((method, path))
        if method == "POST":
            self.posted += 1
            message_id = f"message-{self.posted}"
            self.contents[message_id] = "" if payload is None else payload["content"]
            return {"id": message_id}
        message_id = path.split("/messages/")[1].split("/")[0].split("?")[0]
        if method == "DELETE":
            if self.contents.pop(message_id, None) is None:
                raise _NOT_FOUND
            return None
        if "/reactions/" in path:
            users = self.reactions.get((message_id, unquote(path.split("/reactions/")[1].split("?")[0])))
            if users is None:
                raise _NOT_FOUND
            return [{"id": user, "bot": False} for user in users]
        content = self.contents.get(message_id)
        if content is None:
            raise _NOT_FOUND
        return {"id": message_id, "content": content}

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]


class _FakeDirectory:
    """What the shared directory answers for this bot — no Discord, no guild scan."""

    def owner_dm(self) -> str:
        raise AssertionError("the skill supply chain must never open a DM (SI-6)")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


def _deploy_bindings(skill: str) -> skill_gate_surface.SupplyChainSurface:
    """The deploy gate's declared surface, stubbed at the directory boundary."""
    return skill_gate_surface.SupplyChainSurface(
        skill_gate_surface.deploy_kind(skill), _OWNER_ID, _FakeDirectory()
    )


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDiscord:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    fake = FakeDiscord()
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "logs" / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _deploy_bindings)
    monkeypatch.setattr(skill_gate, "_api", fake.api)
    monkeypatch.setenv("SUDO_USER", _ACTOR)
    return fake


def _pending(tmp_path: Path) -> Path:
    return tmp_path / "skill-gate" / "pending" / f"{_SKILL}.json"


def _record(tmp_path: Path) -> dict[str, str]:
    decoded = json.loads(_pending(tmp_path).read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in decoded.items()}


def _audit_lines(tmp_path: Path) -> list[dict[str, str]]:
    path = tmp_path / "logs" / "approval-abandons.jsonl"
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    return [json.loads(line) for line in raw.splitlines() if line]


def _request_args(*, digest: str = _DIGEST) -> argparse.Namespace:
    return argparse.Namespace(skill=_SKILL, hash=digest, fresh=False, json=False)


def _consume_args(*, digest: str = _DIGEST, message_id: str = "message-1") -> argparse.Namespace:
    return argparse.Namespace(skill=_SKILL, hash=digest, message_id=message_id)


def _abandon_args(*, digest: str = _DIGEST, message_id: str = "message-1") -> argparse.Namespace:
    return argparse.Namespace(skill=_SKILL, hash=digest, message_id=message_id, reason=_REASON)


def _post_live_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDiscord:
    """Given-block helper: exactly the production state — one live request, owner ✅."""
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    fake.reactions[("message-1", skill_gate_specs.APPROVE_EMOJI)] = [_OWNER_ID]
    return fake


def test_consume_when_record_still_binds_the_mounted_triple_then_the_record_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the owner approved and the deploy mounted this exact (skill, hash, message id).
    fake = _post_live_request(tmp_path, monkeypatch)
    _ = capsys.readouterr()

    # When: the MOUNT stage retires the decision it just consumed.
    result = skill_gate.cmd_consume(_consume_args())

    # Then: the record is gone, the owner's message is untouched, and the token names the skill.
    captured = capsys.readouterr()
    assert result == 0
    assert not _pending(tmp_path).exists()
    assert f"CONSUMED skill={_SKILL}" in captured.out
    assert "DELETE" not in fake.methods()
    assert "message-1" in fake.contents


def test_consume_when_the_record_was_replaced_in_between_then_it_is_a_noop_that_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the record moved on to another digest/message after this mount was authorized.
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    assert skill_gate.cmd_request(_request_args(digest=_OTHER_DIGEST)) == 0
    replaced = _record(tmp_path)
    _ = capsys.readouterr()

    # When: the older mount tries to consume its own (hash, message id).
    result = skill_gate.cmd_consume(_consume_args())

    # Then: compare-and-swap declines — the newer record survives byte-identical.
    captured = capsys.readouterr()
    assert result == 0
    assert "CONSUME-NOOP" in captured.out
    assert "reason=record-superseded" in captured.out
    assert _record(tmp_path) == replaced
    assert fake.posted == 2


def test_consume_when_no_record_exists_then_it_is_a_noop_that_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: nothing pending for this skill (a re-run, or a record already retired).
    fake = _install(tmp_path, monkeypatch)

    # When: the MOUNT stage consumes anyway.
    result = skill_gate.cmd_consume(_consume_args())

    # Then: absence is benign — exit 0, a NOOP token, and Discord is never touched.
    captured = capsys.readouterr()
    assert result == 0
    assert "reason=record-absent" in captured.out
    assert fake.calls == []


def test_consume_when_the_record_is_unreadable_then_it_prints_its_token_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the pending record cannot be decoded — unreadable is NEVER "absent".
    fake = _install(tmp_path, monkeypatch)
    _pending(tmp_path).parent.mkdir(mode=0o700, parents=True)
    _ = _pending(tmp_path).write_text("{not json", encoding="utf-8")

    # When: the MOUNT stage consumes.
    result = skill_gate.cmd_consume(_consume_args())

    # Then: a machine-readable failure token on stderr, and the debris is left for the operator.
    captured = capsys.readouterr()
    assert result == skill_gate_retire.RETIREMENT_REFUSAL_EXIT
    assert "CONSUME-FAILED" in captured.err
    assert "reason=store-unreadable" in captured.err
    assert captured.out == ""
    assert _pending(tmp_path).read_text(encoding="utf-8") == "{not json"
    assert fake.calls == []


def test_consume_when_the_key_lease_is_held_elsewhere_then_it_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: another holder owns this key's lease — the same lease the request path takes.
    fake = _post_live_request(tmp_path, monkeypatch)
    first = _record(tmp_path)
    lease_root = skill_gate.GATE_DIR / skill_gate_request.LEASE_DIRNAME
    lease_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = (lease_root / f"skill-deploy%3a{_SKILL}.lease").open("a", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    _ = capsys.readouterr()

    # When: consume runs while the lease is unavailable.
    try:
        result = skill_gate.cmd_consume(_consume_args())
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    # Then: no mutation happens without the lease, and the failure is named, not raised.
    captured = capsys.readouterr()
    assert result == skill_gate_retire.RETIREMENT_REFUSAL_EXIT
    assert "reason=lease-held" in captured.err
    assert _record(tmp_path) == first
    assert "DELETE" not in fake.methods()


def test_request_after_consume_when_the_owner_had_decided_then_the_next_request_posts_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an owner-decided record — the exact state that refused with reason=owner-decided —
    # retired by the mount that consumed it.
    fake = _post_live_request(tmp_path, monkeypatch)
    assert skill_gate.cmd_consume(_consume_args()) == 0
    _ = capsys.readouterr()

    # When: the next deploy of the same skill requests approval for a new artifact.
    result = skill_gate.cmd_request(_request_args(digest=_OTHER_DIGEST))

    # Then: the block is gone — a fresh message is posted and bound to the new digest.
    captured = capsys.readouterr()
    assert result == 0
    assert fake.posted == 2
    assert captured.out == "message-2\n"
    assert _record(tmp_path)["hash"] == _OTHER_DIGEST


@pytest.mark.parametrize("named", [_abandon_args(digest=_OTHER_DIGEST), _abandon_args(message_id="message-999")])
def test_abandon_when_a_bound_field_does_not_match_then_it_refuses_and_the_record_survives(
named: argparse.Namespace,
tmp_path: Path,
monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a stored record the operator names with the wrong digest, or the wrong message id.
    fake = _post_live_request(tmp_path, monkeypatch)
    first = _record(tmp_path)
    _ = capsys.readouterr()

    # When: abandon is attempted on that mismatched field.
    result = skill_gate.cmd_abandon(named)

    # Then: never a blind delete — the record, the message and the audit log are all untouched.
    captured = capsys.readouterr()
    assert result == skill_gate_retire.RETIREMENT_REFUSAL_EXIT
    assert "ABANDON-REFUSED" in captured.err
    assert "reason=binding-mismatch" in captured.err
    assert _record(tmp_path) == first
    assert _audit_lines(tmp_path) == []
    assert "DELETE" not in fake.methods()


def test_abandon_when_the_skill_names_no_record_then_it_refuses_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: no pending record under this skill at all.
    _ = _install(tmp_path, monkeypatch)

    # When: abandon is attempted.
    result = skill_gate.cmd_abandon(_abandon_args())

    # Then: there is nothing to override, and the refusal says so.
    captured = capsys.readouterr()
    assert result == skill_gate_retire.RETIREMENT_REFUSAL_EXIT
    assert "reason=record-absent" in captured.err
    assert _audit_lines(tmp_path) == []


def test_abandon_when_every_field_matches_then_the_record_is_dropped_and_the_override_is_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a record the owner decided but whose effect can never run.
    fake = _post_live_request(tmp_path, monkeypatch)
    _ = capsys.readouterr()

    # When: the operator abandons it with a reason.
    result = skill_gate.cmd_abandon(_abandon_args())

    # Then: the record is retired AND one durable audit line carries who/what/why.
    captured = capsys.readouterr()
    assert result == 0
    assert not _pending(tmp_path).exists()
    assert f"ABANDONED skill={_SKILL}" in captured.out
    audited = _audit_lines(tmp_path)
    assert len(audited) == 1
    assert audited[0]["skill"] == _SKILL
    assert audited[0]["hash"] == _DIGEST
    assert audited[0]["message_id"] == "message-1"
    assert audited[0]["reason"] == _REASON
    assert audited[0]["actor"] == _ACTOR
    assert audited[0]["key"] == f"skill-deploy:{_SKILL}"
    assert audited[0]["timestamp"]
    assert fake.contents["message-1"].startswith(f"[skill-deploy] {_SKILL} 배포 승인 요청")


def test_abandon_when_it_succeeds_then_it_never_issues_a_discord_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a live owner-decided request.
    fake = _post_live_request(tmp_path, monkeypatch)
    posted_calls = len(fake.calls)
    _ = capsys.readouterr()

    # When: the operator abandons the record.
    assert skill_gate.cmd_abandon(_abandon_args()) == 0

    # Then: the owner's decision stays visible — abandon made no Discord call whatsoever.
    assert fake.calls[posted_calls:] == []
    assert "message-1" in fake.contents


def test_abandon_when_the_audit_line_cannot_be_written_then_the_record_is_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the audit destination is unwritable (its parent is a regular file).
    _ = _post_live_request(tmp_path, monkeypatch)
    first = _record(tmp_path)
    blocked = tmp_path / "blocked"
    _ = blocked.write_text("", encoding="utf-8")
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", blocked / "approvals.jsonl")
    _ = capsys.readouterr()

    # When: the operator abandons the record.
    result = skill_gate.cmd_abandon(_abandon_args())

    # Then: no unaudited deletion — the audit is written BEFORE the record is retired.
    captured = capsys.readouterr()
    assert result == skill_gate_retire.RETIREMENT_REFUSAL_EXIT
    assert "reason=audit-failed" in captured.err
    assert _record(tmp_path) == first


def test_legacy_abandon_refuses_a_current_schema_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a current, fully bound pending record.
    _ = _post_live_request(tmp_path, monkeypatch)
    args = _abandon_args()
    args.legacy_only = True
    before = _pending(tmp_path).read_bytes()
    _ = capsys.readouterr()

    # When: the owner-only legacy closure path is used on it.
    result = skill_gate.cmd_abandon(args)

    # Then: the guard preserves the modern record and names the mismatch.
    captured = capsys.readouterr()
    assert result == skill_gate_retire.RETIREMENT_REFUSAL_EXIT
    assert "reason=not-legacy" in captured.err
    assert _pending(tmp_path).read_bytes() == before


def test_deploy_pipeline_when_it_consumes_after_mount_then_the_call_is_staged_and_non_fatal() -> None:
    # Given: the deploy pipeline that must retire the decision its MOUNT stage consumed.
    script = (Path(__file__).resolve().parents[2] / "automation" / "deploy-skill.sh").read_text(encoding="utf-8")
    invocations = [line for line in script.splitlines() if "consume --skill" in line]

    # When/Then: exactly one consume call, its status captured — a failed consume never
    # rolls back a mount that already succeeded.
    assert len(invocations) == 1
    assert "|| CONSUMED=$?" in invocations[0]
    assert not [line for line in script.splitlines() if "CONSUME-WARN" in line and "die " in line]

    # Then: the module is staged with the other gate helpers — an unstaged import would
    # fail EVERY deploy closed, including the deploy shipping this fix.
    helpers = script.partition("GATE_HELPERS=(")[2].partition(")")[0]
    assert "skill_gate_retire.py" in helpers.split()


def test_deploy_pipeline_when_mount_succeeds_then_consume_precedes_post_install_verification() -> None:
    # Given: Stage 4 installs the approved artifact before post-install checks run.
    lines = (Path(__file__).resolve().parents[2] / "automation" / "deploy-skill.sh").read_text(
        encoding="utf-8"
    ).splitlines()

    # When: the install, decision retirement, and skill-list verification line indices are compared.
    install = next(index for index, line in enumerate(lines) if 'install_reviewed_skill "$SRC_DIR"' in line)
    consume = next(index for index, line in enumerate(lines) if "consume --skill" in line)
    listed = next(index for index, line in enumerate(lines) if 'hermes_lists_skill agent "$SKILL"' in line)

    # Then: the approval's effect is realized at install; consume must not sit behind post-install verification.
    assert install < consume < listed, (
        "the approval's effect is realized at install; consume must not sit behind post-install verification"
    )
