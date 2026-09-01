"""VA-2 release-authorize: one release ✅ authorizes exactly the digests it displayed.

The staged gate's ``release-authorize`` replaces the per-skill owner ✅ when a deploy
rides a release approval. What it must never do is authorize adjacent bytes: a skill
that changed after the release cut is not in the approval's action-hash preimage and is
refused BEFORE any Discord round-trip (fail-closed, exit 4).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote

import pytest

import automation.skill_gate as skill_gate
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
)
from automation.release_spec import ReleaseSpec

_OWNER = "280680578314010625"
_CHANNEL = "1528936606856122421"
_MESSAGE = "1538547247514525816"
_DIGEST = "d" * 64
_APPROVE = quote("✅")
_CANCEL = quote("⛔")


def _spec() -> ReleaseSpec:
    return ReleaseSpec(
        version="v1.2.3",
        head_sha="a" * 40,
        release_nonce="b" * 32,
        surface_digests=(("skill:meeting", _DIGEST), ("home:skills/mail", "c" * 64)),
        patch_notes="- meeting skill",
    )


def _env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    reactions: dict[str, list[dict[str, object]]],
    api_log: list[str] | None = None,
    write_record: bool = True,
) -> None:
    gate_dir = tmp_path / "skill-gate"
    (gate_dir / "pending").mkdir(parents=True)
    interop = tmp_path / "config.json"
    _ = interop.write_text(
        json.dumps({"owner_id": _OWNER, "deploy_approvals_channel_id": _CHANNEL}),
        encoding="utf-8",
    )
    monkeypatch.setattr(skill_gate, "GATE_DIR", gate_dir)
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "DUMMY-release-authorize")

    spec = _spec()
    if write_record:
        binding = ApprovalBinding(
            ApprovalKind.RELEASE, ApprovalSurface.SKILL_APPROVALS, _CHANNEL, POLICY_VERSION
        )
        record = spec.new_record(_MESSAGE, binding)
        _ = (gate_dir / "pending" / "release.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
    content = spec.render()

    def api(method: str, path: str, payload: dict[str, object] | None = None) -> object:
        del payload
        if api_log is not None:
            api_log.append(f"{method} {path}")
        if path == f"/channels/{_CHANNEL}":
            return {"id": _CHANNEL, "type": 0, "name": "approvals", "recipients": []}
        if path == f"/channels/{_CHANNEL}/messages/{_MESSAGE}":
            return {
                "id": _MESSAGE,
                "content": content,
                "timestamp": "2026-08-30T12:00:00.000000+00:00",
            }
        for emoji, users in reactions.items():
            if path.startswith(f"/channels/{_CHANNEL}/messages/{_MESSAGE}/reactions/{emoji}"):
                return users
        return []

    monkeypatch.setattr(skill_gate, "_api", api)


def _run(skill: str = "meeting", digest: str = _DIGEST) -> int:
    return skill_gate.cmd_release_authorize(argparse.Namespace(skill=skill, hash=digest))


def test_an_approved_release_authorizes_a_covered_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _env(
        tmp_path,
        monkeypatch,
        reactions={_APPROVE: [{"id": _OWNER, "bot": False}], _CANCEL: []},
    )

    rc = _run()

    assert rc == 0
    out = capsys.readouterr()
    assert out.out.strip() == f"{_MESSAGE}:{'b' * 32}"
    assert "RELEASE-AUTHORIZED" in out.err


def test_an_uncovered_digest_is_refused_before_any_discord_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_log: list[str] = []
    _env(tmp_path, monkeypatch, reactions={}, api_log=api_log)

    rc = _run(digest="e" * 64)

    assert rc == skill_gate.RELEASE_NOT_COVERED_EXIT
    assert api_log == []
    assert "RELEASE-AUTHORIZE-BLOCK" in capsys.readouterr().err


def test_an_uncovered_skill_name_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(tmp_path, monkeypatch, reactions={})

    assert _run(skill="mail") == skill_gate.RELEASE_NOT_COVERED_EXIT


def test_a_cancelled_release_returns_the_denied_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ⛔ 우선 — ✅ 가 함께 있어도 취소다(외부효과 fail-safe 불변식).
    _env(
        tmp_path,
        monkeypatch,
        reactions={
            _CANCEL: [{"id": _OWNER, "bot": False}],
            _APPROVE: [{"id": _OWNER, "bot": False}],
        },
    )

    assert _run() == skill_gate.DENIED_EXIT


def test_a_pending_release_does_not_authorize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(tmp_path, monkeypatch, reactions={_APPROVE: [], _CANCEL: []})

    assert _run() == 1


def test_a_missing_release_record_is_a_distinct_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(tmp_path, monkeypatch, reactions={}, write_record=False)

    assert _run() == 2


def test_an_approved_release_without_a_valid_peer_attestation_is_exit_seven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """✅ 만으로는 부족하다 — peer 증명이 없으면 7(만료/부재)로 갱신을 요구한다."""
    _env(
        tmp_path,
        monkeypatch,
        reactions={_APPROVE: [{"id": _OWNER, "bot": False}], _CANCEL: []},
    )

    rc = skill_gate.cmd_release_authorize(
        argparse.Namespace(
            skill="meeting",
            hash=_DIGEST,
            peer_attest_mode="signed",
            peer_attest_public_key="",
            peer_attestation_stdin=False,
        )
    )

    assert rc == 7


def test_a_non_owner_reaction_never_authorizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(
        tmp_path,
        monkeypatch,
        reactions={_APPROVE: [{"id": "999", "bot": False}, {"id": _OWNER, "bot": True}]},
    )

    assert _run() == 1
