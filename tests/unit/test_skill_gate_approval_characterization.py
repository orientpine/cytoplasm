"""Characterization tests: CURRENT observable behavior of the two approval gates.

Locks the deploy gate (``automation.skill_gate``) and the managed-skill publish gate
(``automation.skill_gate_publish``) request/check contracts — record shape, stdout bytes,
fast-path, check verdicts — including the defects explicitly marked below.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation import peer_attestation, skill_gate, skill_gate_publish, skill_gate_specs, skill_gate_surface
from automation.interop.approval_surface import POLICY_VERSION, ApprovalKind, ChannelFacts
from automation.peer_attestation import format_attestation

_DIGEST = "a" * 64
_OTHER_DIGEST = "c" * 64
_MANIFEST_DIGEST = "b" * 64
_NONCE = "1" * 32
_OWNER_ID = "111111111111111111"
_STRANGER_ID = "999999999999999999"
_AGENT_BOT_ID = "222222222222222222"
_PEER_BOT_ID = "333333333333333333"
_CHANNEL_ID = "100000000000000009"
_SKILL = "calendar"
_PUBLISH_SKILL = "managed-x"
_TAG = "managed-x/v1"
_REQUEST_TIMESTAMP = "2026-07-17T00:00:00.000000+00:00"
_ATTESTATION_TIMESTAMP = "2026-07-17T00:01:00.000000+00:00"
_HEX32 = re.compile(r"[0-9a-f]{32}")
_PASS_REVIEW = "- review: ✅ PASS (frontmatter/scenario/secret_scan/content_digest 4/4, sha256-bound)"


class _FakeDirectory:
    """What the shared directory answers for this bot — no Discord, no guild scan."""

    def owner_dm(self) -> str:
        raise AssertionError("the skill supply chain must never open a DM (SI-6)")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


def _surface(kind: ApprovalKind) -> skill_gate_surface.SupplyChainSurface:
    return skill_gate_surface.SupplyChainSurface(kind, _OWNER_ID, _FakeDirectory())


def _bind_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both gates now DECLARE a kind; this stubs only the directory behind them."""
    monkeypatch.setattr(
        skill_gate, "_deploy_bindings", lambda skill: _surface(skill_gate_surface.deploy_kind(skill))
    )
    monkeypatch.setattr(
        skill_gate_publish, "_publish_bindings", lambda: _surface(ApprovalKind.SKILL_PUBLISH)
    )


_BINDING_FIELDS = {
    "channel_id": _CHANNEL_ID,
    "policy_version": str(POLICY_VERSION),
    "surface": "skill-approvals",
}


def _install_api_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Seam used by the existing gate tests: patch GATE_DIR + bindings + ``_api``."""
    calls: list[tuple[str, str]] = []

    def discord_api(method: str, path: str, _payload: dict[str, str] | None = None) -> dict[str, str]:
        calls.append((method, path))
        return {"id": f"message-{len(calls)}"}

    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    _bind_surfaces(monkeypatch)
    monkeypatch.setattr(skill_gate, "_api", discord_api)
    return calls


def _pending(tmp_path: Path, name: str) -> Path:
    return tmp_path / "skill-gate" / "pending" / f"{name}.json"


def _request_args(*, digest: str = _DIGEST, fresh: bool = False, json_output: bool = False) -> argparse.Namespace:
    return argparse.Namespace(skill=_SKILL, hash=digest, fresh=fresh, json=json_output)


def _publish_args(*, json_output: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        skill=_PUBLISH_SKILL, hash=_DIGEST, manifest_hash=_MANIFEST_DIGEST, tag=_TAG, json=json_output
    )


def test_cmd_request_when_first_call_then_pending_record_field_set_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a stubbed #approvals channel and an empty gate dir.
    calls = _install_api_stub(tmp_path, monkeypatch)

    # When: a deploy approval is requested once.
    assert skill_gate.cmd_request(_request_args()) == 0

    # Then: exactly three fields are stored, serialized in insertion order (no sort_keys).
    raw = _pending(tmp_path, _SKILL).read_text(encoding="utf-8")
    state = json.loads(raw)
    assert sorted(state.keys()) == [
        "action_hash", "approval_action", "approval_destination", "channel_id", "deploy_nonce", "hash", "kind", "message_id", "policy_version", "surface"
    ]
    assert state["hash"] == _DIGEST
    assert state["message_id"] == "message-1"
    assert _HEX32.fullmatch(state["deploy_nonce"]) is not None
    assert {name: state[name] for name in _BINDING_FIELDS} == _BINDING_FIELDS
    assert state["kind"] == "skill-deploy"
    assert raw == json.dumps({
        "deploy_nonce": state["deploy_nonce"],
        "hash": _DIGEST,
        "message_id": "message-1",
        "action_hash": state["action_hash"],
        "approval_action": "skill.deploy",
        "approval_destination": "skill:calendar",
        "channel_id": _CHANNEL_ID,
        "kind": "skill-deploy",
        "policy_version": str(POLICY_VERSION),
        "surface": "skill-approvals",
    })
    assert calls == [("POST", f"/channels/{_CHANNEL_ID}/messages")]


def test_cmd_request_when_plain_then_stdout_is_bare_message_id_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a stubbed channel returning message-1.
    _install_api_stub(tmp_path, monkeypatch)

    # When: the request runs without --json.
    assert skill_gate.cmd_request(_request_args()) == 0

    # Then: stdout is byte-exactly the message id plus a newline, stderr empty.
    captured = capsys.readouterr()
    assert captured.out == "message-1\n"
    assert captured.err == ""


def test_cmd_request_when_json_flag_then_stdout_is_sorted_json_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a stubbed channel returning message-1.
    _install_api_stub(tmp_path, monkeypatch)

    # When: the request runs with --json.
    assert skill_gate.cmd_request(_request_args(json_output=True)) == 0

    # Then: stdout is the sorted-key JSON of the stored record plus a newline.
    state = json.loads(_pending(tmp_path, _SKILL).read_text(encoding="utf-8"))
    out = capsys.readouterr().out
    assert out == json.dumps(state, sort_keys=True) + "\n"
    assert json.loads(out)["message_id"] == "message-1"


def test_cmd_request_when_same_hash_repeated_then_reuses_record_without_second_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: one stored pending record for this skill/hash.
    calls = _install_api_stub(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    first = json.loads(_pending(tmp_path, _SKILL).read_text(encoding="utf-8"))
    _ = capsys.readouterr()

    # When: the identical request is issued again.
    assert skill_gate.cmd_request(_request_args()) == 0

    # Then: the fast path reprints the stored id and posts nothing new.
    assert capsys.readouterr().out == "message-1\n"
    assert len(calls) == 1
    assert json.loads(_pending(tmp_path, _SKILL).read_text(encoding="utf-8")) == first


def test_cmd_request_when_hash_differs_then_live_message_is_probed_before_rebinding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a pending record already bound to message-1.
    calls = _install_api_stub(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    first = json.loads(_pending(tmp_path, _SKILL).read_text(encoding="utf-8"))

    # When: the same skill is requested again under a different hash.
    assert skill_gate.cmd_request(_request_args(digest=_OTHER_DIGEST)) == 0

    # Then (TRANSFERRED from the characterized overwrite defect): the gate no longer replaces
    # the record blind — it PROBES the stored message first. This stub answers every GET with a
    # bare id, so message-1 reads as already gone and no DELETE is needed; the DELETE-before-POST
    # ordering for a still-live message is proven by tests/unit/test_skill_gate_single_live_request
    # .py::test_different_hash_when_request_is_live_then_delete_precedes_post_and_one_record_remains.
    second = json.loads(_pending(tmp_path, _SKILL).read_text(encoding="utf-8"))
    assert [method for method, _ in calls] == ["POST", "GET", "POST"]
    assert first["message_id"] == "message-1"
    assert second["message_id"] == "message-3"
    assert second["hash"] == _OTHER_DIGEST
    assert second["deploy_nonce"] != first["deploy_nonce"]


def test_cmd_request_when_fresh_then_supersedes_the_live_request_for_the_same_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a pending record that the fast path would otherwise reuse verbatim.
    calls = _install_api_stub(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    first = json.loads(_pending(tmp_path, _SKILL).read_text(encoding="utf-8"))
    _ = capsys.readouterr()

    # When: the same hash is requested with --fresh.
    assert skill_gate.cmd_request(_request_args(fresh=True)) == 0

    # Then (TRANSFERRED from the characterized orphan defect): --fresh no longer merely bypasses
    # the fast path — it supersedes, probing the stored message before posting so it can never be
    # orphaned. Under this stub the probe reads message-1 as gone, so no DELETE is issued; the
    # supersede-with-DELETE path is proven by tests/unit/test_skill_gate_single_live_request.py::
    # test_fresh_when_request_is_live_then_supersedes_the_old_message_instead_of_orphaning_it.
    second = json.loads(_pending(tmp_path, _SKILL).read_text(encoding="utf-8"))
    assert capsys.readouterr().out == "message-3\n"
    assert [method for method, _ in calls] == ["POST", "GET", "POST"]
    assert second["hash"] == first["hash"] == _DIGEST
    assert second["message_id"] == "message-3"
    assert second["deploy_nonce"] != first["deploy_nonce"]


@dataclass(frozen=True, slots=True)
class _CheckPlan:
    reactions: tuple[dict[str, object], ...]
    attested: bool = True


def _request_record() -> dict[str, object]:
    return {
        "id": "request-1",
        "channel_id": _CHANNEL_ID,
        "timestamp": _REQUEST_TIMESTAMP,
        "author": {"id": _AGENT_BOT_ID, "bot": True},
        "content": skill_gate_specs.DeploySpec(
            skill=_SKILL,
            digest=_DIGEST,
            deploy_nonce=_NONCE,
            review_status=_PASS_REVIEW,
            provenance=skill_gate_specs.provenance_of(""),
            binding=skill_gate._REQUEST_BINDING,
        ).render(),
    }


def _configure_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan: _CheckPlan) -> None:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    peers = tmp_path / "peers.yaml"
    _ = peers.write_text(
        f'peers:\n  agent-cha:\n    bot_user_id: "{_AGENT_BOT_ID}"\n    account: agent\n'
        f'  peer-test:\n    bot_user_id: "{_PEER_BOT_ID}"\n    account: peer\n',
        encoding="utf-8",
    )
    peers.chmod(0o600)
    monkeypatch.setattr(
        peer_attestation,
        "_trusted_owner_uids",
        lambda: frozenset({peers.stat().st_uid}),
    )
    attestations: list[dict[str, object]] = [
        {
            "channel_id": _CHANNEL_ID,
            "timestamp": _ATTESTATION_TIMESTAMP,
            "author": {"id": _PEER_BOT_ID, "bot": True},
            "message_reference": {"message_id": "request-1", "channel_id": _CHANNEL_ID},
            "content": format_attestation(_NONCE, _SKILL, _DIGEST, "PASS"),
        }
    ] if plan.attested else []

    pending_dir = tmp_path / "skill-gate" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    pending_file = pending_dir / "calendar.json"

    _ = pending_file.write_text(
        json.dumps(
            {
                "action_hash": skill_gate_specs._hash(
                    "skill-deploy", "calendar", _DIGEST, "", ""
                ),
                "approval_action": "skill.deploy",
                "approval_destination": "skill:calendar",
                "channel_id": _CHANNEL_ID,
                "deploy_nonce": _NONCE,
                "hash": _DIGEST,
                "kind": "skill-deploy",
                "message_id": "request-1",
                "policy_version": str(POLICY_VERSION),
                "surface": "skill-approvals",
            }
        ),
        encoding="utf-8",
    )

    def discord_api(_method: str, path: str, _payload: dict[str, object] | None = None) -> object:
        if path == f"/channels/{_CHANNEL_ID}/messages/request-1":
            return _request_record()
        if path.startswith(f"/channels/{_CHANNEL_ID}/messages?"):
            return attestations
        if "/reactions/" in path:
            return list(plan.reactions) if "%E2%9C%85" in path else []
        raise AssertionError(path)

    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "OPS_PEERS_CONFIG", peers)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "review_status_line", lambda *_args: _PASS_REVIEW)
    _bind_surfaces(monkeypatch)
    monkeypatch.setattr(skill_gate, "_api", discord_api)
    monkeypatch.setattr(skill_gate, "_now", lambda: skill_gate._parse_timestamp("2026-07-17T00:02:00+00:00"))


def _check_args() -> argparse.Namespace:
    return argparse.Namespace(
        skill=_SKILL,
        hash=_DIGEST,
        message_id="request-1",
        deploy_nonce=_NONCE,
        injection_file="",
        peer_attest_mode="discord",
    )


def test_cmd_check_when_owner_reaction_and_attestation_then_approves_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a peer-attested request carrying the owner's ✅ reaction.
    _configure_check(tmp_path, monkeypatch, _CheckPlan(({"id": _OWNER_ID, "bot": False},)))

    # When: check reads the message, its attestation replies, and its reactions.
    result = skill_gate.cmd_check(_check_args())

    # Then: it approves with a masked-owner line and appends one skill.deploy record.
    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == f"APPROVED method=manual_reaction owner=…{_OWNER_ID[-4:]}\n"
    assert captured.err == ""
    record = json.loads((tmp_path / "approvals.jsonl").read_text(encoding="utf-8"))
    assert record["action"] == "skill.deploy"
    assert record["approval"] == {"channel": "approvals", "message_id": "request-1", "method": "manual_reaction"}
    assert record["binding"] == {
        "action": "skill.deploy",
        "action_hash": skill_gate_specs._hash(
            "skill-deploy", _SKILL, _DIGEST, "", ""
        ),
        "deploy_nonce": _NONCE,
        "destination": f"skill:{_SKILL}",
        "message_id": "request-1",
    }
    assert record["result"] == {"status": "approved"}
    assert record["target_id"] == f"skill:{_SKILL}"


def test_cmd_check_when_peer_attestation_absent_then_rejects_before_reading_reactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the owner reacted but no peer attestation reply exists.
    _configure_check(tmp_path, monkeypatch, _CheckPlan(({"id": _OWNER_ID, "bot": False},), attested=False))

    # When: check runs.
    result = skill_gate.cmd_check(_check_args())

    # Then: attestation is evaluated first and the gate fails closed with no log record.
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "REJECTED: valid peer attestation absent\n"
    assert not (tmp_path / "approvals.jsonl").exists()


def test_cmd_check_when_only_non_owner_reacted_then_rejects_with_ignored_then_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a peer-attested request whose only ✅ reaction comes from a stranger.
    _configure_check(tmp_path, monkeypatch, _CheckPlan(({"id": _STRANGER_ID, "bot": False},)))

    # When: check runs.
    result = skill_gate.cmd_check(_check_args())

    # Then: each foreign reactor is logged as IGNORED, then the gate rejects.
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == (
        f"IGNORED non-owner reaction: user=…{_STRANGER_ID[-4:]} bot=False\n"
        "REJECTED: no owner ✅ reaction on message …st-1\n"
    )
    assert not (tmp_path / "approvals.jsonl").exists()


def test_publish_request_when_posted_then_pending_path_and_field_set_are_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the publish gate rides the deploy gate's channel + ``_api``.
    calls = _install_api_stub(tmp_path, monkeypatch)

    # When: a publish approval is requested.
    assert skill_gate_publish.cmd_publish_request(_publish_args()) == 0

    # Then: the record lands at pending/publish-{skill}.json with the exact binding schema.
    raw = _pending(tmp_path, f"publish-{_PUBLISH_SKILL}").read_text(encoding="utf-8")
    state = json.loads(raw)
    assert sorted(state.keys()) == [
        "action_hash",
        "approval_action",
        "approval_destination",
        "channel_id",
        "hash",
        "kind",
        "manifest_hash",
        "message_id",
        "policy_version",
        "publish_nonce",
        "surface",
        "tag",
    ]
    assert {name: state[name] for name in _BINDING_FIELDS} == _BINDING_FIELDS
    assert state["kind"] == "skill-publish"
    assert raw == json.dumps(state, sort_keys=True)
    assert state["hash"] == _DIGEST
    assert state["manifest_hash"] == _MANIFEST_DIGEST
    assert state["tag"] == _TAG
    assert state["message_id"] == "message-1"
    assert _HEX32.fullmatch(state["publish_nonce"]) is not None
    assert calls == [("POST", f"/channels/{_CHANNEL_ID}/messages")]


def test_publish_request_when_posted_then_stdout_is_message_id_or_sorted_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a stubbed channel.
    _install_api_stub(tmp_path, monkeypatch)

    # When: the request runs plainly, then again with --json.
    assert skill_gate_publish.cmd_publish_request(_publish_args()) == 0
    plain = capsys.readouterr()
    assert skill_gate_publish.cmd_publish_request(_publish_args(json_output=True)) == 0
    structured = capsys.readouterr()

    # Then: plain prints the bare id; --json prints the stored record sorted, both newline-terminated.
    # TRANSFERRED: the second request no longer posts a duplicate (it used to print "message-2"),
    # so --json reprints the SAME live record — see tests/unit/test_skill_gate_single_live_request
    # .py::test_publish_repeat_when_release_is_unchanged_then_no_duplicate_and_id_is_preserved.
    state = json.loads(_pending(tmp_path, f"publish-{_PUBLISH_SKILL}").read_text(encoding="utf-8"))
    assert plain.out == "message-1\n"
    assert plain.err == ""
    assert structured.out == json.dumps(state, sort_keys=True) + "\n"
    assert json.loads(structured.out)["message_id"] == "message-1"


def test_publish_request_when_repeated_then_reuses_the_live_request_without_a_second_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a publish record already bound to message-1.
    calls = _install_api_stub(tmp_path, monkeypatch)
    assert skill_gate_publish.cmd_publish_request(_publish_args()) == 0
    first = json.loads(_pending(tmp_path, f"publish-{_PUBLISH_SKILL}").read_text(encoding="utf-8"))

    # When: the identical publish request is issued a second time.
    assert skill_gate_publish.cmd_publish_request(_publish_args()) == 0

    # Then (TRANSFERRED from the characterized unguarded rewrite): the publish gate now has the
    # same PENDING fast path as the deploy gate, so an unchanged release reuses its live message
    # instead of replacing message_id — see tests/unit/test_skill_gate_single_live_request.py::
    # test_publish_repeat_when_release_is_unchanged_then_no_duplicate_and_id_is_preserved.
    second = json.loads(_pending(tmp_path, f"publish-{_PUBLISH_SKILL}").read_text(encoding="utf-8"))
    assert [method for method, _ in calls] == ["POST"]
    assert first["message_id"] == "message-1"
    assert second["message_id"] == "message-1"
    assert second["publish_nonce"] == first["publish_nonce"]
    assert second["hash"] == first["hash"] == _DIGEST
