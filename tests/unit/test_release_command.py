"""A release starts from the owner's own Discord message, not from a ✅ card.

Owner 2026-11-02 21:35 KST (voice, Q447): "릴리즈도 승인 필요 없을 것 같아. 내라고 하면 네가
해서 릴리즈하고 배포까지 하는 걸로 하자." These cases drive the real node half
(`release_approval command` → `decision` → `release-authorize`) against a fake Discord: the
owner's message must be his own non-bot message in the release server, at most a day old,
naming a release or a deploy (a voice message through its receiver transcript), and used
for one HEAD only. Anything else records nothing, so nothing can be tagged or deployed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import pytest

import automation.release_command as release_command
import automation.skill_gate as skill_gate
from automation import release_approval
from automation.release_spec import ReleaseSpec

_OWNER = "1500000000000000004"
_GUILD = "1500000000000000001"
_APPROVALS = "1500000000000000003"
_THREAD = "1500000000000000002"
_DIGEST = "d" * 64
_EPOCH_MS = 1420070400000
_AUDIO = b"OggS voice bytes"
_CARD_BOT = "1500000000000000005"


def _snowflake(seconds_ago: float) -> str:
    return str((int((time.time() - seconds_ago) * 1000) - _EPOCH_MS) << 22)


def _spec(head: str = "a" * 40) -> ReleaseSpec:
    return ReleaseSpec(
        version="v1.2.3", head_sha=head, release_nonce="b" * 32,
        surface_digests=(("skill:meeting", _DIGEST),), patch_notes="- meeting skill",
    )


class FakeDiscord:
    def __init__(self, message: dict[str, object], *, guild: str = _GUILD) -> None:
        self.message = message
        self.guild = guild
        self.fail = False
        self.posts: list[str] = []

    def __call__(self, method: str, path: str, payload: dict[str, object] | None = None) -> object:
        del payload
        if method != "GET":
            self.posts.append(path)
            return {"id": "1"}
        if self.fail:
            raise OSError("discord unreachable")
        if path == f"/channels/{_APPROVALS}":
            return {"id": _APPROVALS, "type": 0, "guild_id": _GUILD, "name": "yoodori"}
        if path == f"/channels/{_THREAD}":
            return {"id": _THREAD, "type": 11, "guild_id": self.guild}
        if path == f"/channels/{_THREAD}/messages/{self.message['id']}":
            return dict(self.message)
        raise OSError(f"unexpected path {path}")


def _gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, discord: FakeDiscord) -> Path:
    gate_dir = tmp_path / "skill-gate"
    (gate_dir / "pending").mkdir(parents=True)
    interop = tmp_path / "config.json"
    _ = interop.write_text(
        json.dumps({"owner_id": _OWNER, "deploy_approvals_channel_id": _APPROVALS}), encoding="utf-8",
    )
    monkeypatch.setattr(skill_gate, "GATE_DIR", gate_dir)
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", gate_dir / "approvals.jsonl")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "DUMMY-release-command")
    monkeypatch.setattr(skill_gate, "_api", discord)
    monkeypatch.setattr(release_command, "_download", lambda url: _AUDIO)
    return gate_dir


def _message(content: str, *, seconds_ago: float = 60, author: str = _OWNER, bot: bool = False,
             voice: bool = False) -> dict[str, object]:
    message: dict[str, object] = {
        "id": _snowflake(seconds_ago), "content": content,
        "author": {"id": author, "bot": bot},
    }
    if voice:
        message |= {"flags": 1 << 13, "attachments": [{
            "url": "https://cdn.example.invalid/voice-message.ogg",
            "filename": "voice-message.ogg", "content_type": "audio/ogg",
        }]}
    return message


def _card(question: str, answer: str, *, by: str = "버튼", seconds_ago: float = 60, author: str = _CARD_BOT,
          bot: bool = True, webhook: bool = False, folded: bool = True) -> dict[str, object]:
    """A dori question card (dori ask) as Discord shows it once the owner's tap folded it into his answer."""
    text = (f"✅ **Q37** {question}\n→ **{answer}**\n-# {by} · 11. 12. 15:04" if folded
            else f"<@{_OWNER}> **Q37** {question}")
    message: dict[str, object] = {
        "id": _snowflake(seconds_ago), "channel_id": _THREAD, "content": "", "flags": 1 << 15,
        "author": {"id": author, "bot": bot}, "edited_timestamp": "2026-11-12T06:04:50.320000+00:00",
        "components": [{"type": 17, "components": [{"type": 10, "content": text}]}],
    }
    if webhook:
        message["webhook_id"] = author
    return message


def _plan(tmp_path: Path, message: dict[str, object], *, guild: str = _GUILD,
          transcript: str | None = None, head: str = "a" * 40) -> str:
    spec = _spec(head)
    payload: dict[str, object] = {
        "version": spec.version, "head": spec.head_sha,
        "surface_digests": [list(row) for row in spec.surface_digests],
        "patch_notes": spec.patch_notes,
        "owner_command_url": f"https://discord.com/channels/{guild}/{_THREAD}/{message['id']}",
    }
    if transcript is not None:
        payload["owner_command_transcript"] = transcript
    path = tmp_path / f"plan-{head[:4]}.json"
    _ = path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _command(plan: str) -> int:
    return release_approval.cmd_command(argparse.Namespace(plan_file=plan))


def _record(gate_dir: Path) -> dict[str, str]:
    return json.loads((gate_dir / "pending/release.json").read_text(encoding="utf-8"))


def _decision(head: str = "a" * 40) -> int:
    return release_approval.cmd_decision(argparse.Namespace(
        head=head, notify_stale=False, completion_candidate=False, tagged="",
    ))


def _authorize(digest: str = _DIGEST, head: str = "a" * 40) -> int:
    return skill_gate.cmd_release_authorize(argparse.Namespace(
        skill=None, surface="skill:meeting", hash=digest, head=head, peer_attest_mode="",
    ))


def test_an_owner_text_command_records_the_release_and_authorizes_its_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    discord = FakeDiscord(_message("이번 거 릴리스 해줘"))
    gate_dir = _gate(tmp_path, monkeypatch, discord)

    assert _command(_plan(tmp_path, discord.message)) == 0

    record = _record(gate_dir)
    assert record["approval_basis"] == "owner-command"
    assert record["owner_command_kind"] == "text"
    assert record["owner_command_text_sha256"] == hashlib.sha256("이번 거 릴리스 해줘".encode()).hexdigest()
    assert record["owner_command_url"].endswith(f"/{_GUILD}/{_THREAD}/{discord.message['id']}")
    assert record["action_hash"] == _spec().action_hash()
    assert discord.posts == []  # no card, no thread
    assert _decision() == 0
    assert _authorize() == 0
    assert _authorize(digest="e" * 64) == 4


def test_an_owner_voice_command_passes_through_its_transcript_and_keeps_both_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    words = "그리고 릴리즈도 승인 필요 없을 것 같아. 내라고 하면 네가 해서 릴리즈하고 배포까지 하는 걸로 하자."
    discord = FakeDiscord(_message("", voice=True))
    gate_dir = _gate(tmp_path, monkeypatch, discord)

    assert _command(_plan(tmp_path, discord.message, transcript=words)) == 0

    record = _record(gate_dir)
    assert record["owner_command_kind"] == "voice"
    assert record["owner_command_audio_sha256"] == hashlib.sha256(_AUDIO).hexdigest()
    assert record["owner_command_transcript_sha256"] == hashlib.sha256(words.encode()).hexdigest()
    assert _decision() == 0


@pytest.mark.parametrize(
    ("message", "transcript", "guild"),
    [
        pytest.param(_message("릴리스 해줘", author="1" * 18), None, _GUILD, id="not-the-owner"),
        pytest.param(_message("릴리스 해줘", bot=True), None, _GUILD, id="bot-message"),
        pytest.param(_message("릴리스 해줘", seconds_ago=25 * 3600), None, _GUILD, id="older-than-a-day"),
        pytest.param(_message("그거 내"), None, _GUILD, id="no-release-word"),
        pytest.param(_message("", voice=True), None, _GUILD, id="voice-without-transcript"),
        pytest.param(_message("", voice=True), "그거 내", _GUILD, id="voice-transcript-without-release-word"),
        pytest.param(_message("릴리스 해줘"), None, "9" * 19, id="another-server"),
    ],
)
def test_a_message_that_cannot_command_a_release_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    message: dict[str, object], transcript: str | None, guild: str,
) -> None:
    discord = FakeDiscord(message, guild=guild)
    gate_dir = _gate(tmp_path, monkeypatch, discord)

    assert _command(_plan(tmp_path, message, guild=guild, transcript=transcript)) == 4

    assert not (gate_dir / "pending/release.json").exists()
    assert discord.posts == []
    assert _authorize() == 2


def test_the_owners_tap_on_a_release_card_records_the_release_and_authorizes_its_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Owner 2026-11-12 (#soodori): "봇 카드 버튼 답도 받게 해." The card is another Dori bot's, not the node's own.
    discord = FakeDiscord(_card("사이토플라즘 릴리스 낼까? 들어갈 것: #237 + #238", "릴리스 해"))
    gate_dir = _gate(tmp_path, monkeypatch, discord)

    assert _command(_plan(tmp_path, discord.message)) == 0

    record = _record(gate_dir)
    text = "✅ **Q37** 사이토플라즘 릴리스 낼까? 들어갈 것: #237 + #238\n→ **릴리스 해**\n-# 버튼 · 11. 12. 15:04"
    assert record["owner_command_kind"] == "card"
    assert record["owner_command_card_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert record["owner_command_card_bot_id"] == _CARD_BOT
    assert discord.posts == []
    assert _decision() == 0
    assert _authorize() == 0


@pytest.mark.parametrize(
    "card",
    [
        pytest.param(_card("사이토플라즘 릴리스 낼까?", "나중에"), id="later-answer"),
        pytest.param(_card("사이토플라즘 릴리스 낼까?", "릴리스는 미뤄"), id="defer-answer-naming-release"),
        pytest.param(_card("이 초안 올릴까?", "올려"), id="card-without-release-word"),
        pytest.param(_card("메일 정리할까?", "릴리스 해"), id="question-without-release-word"),
        pytest.param(_card("사이토플라즘 릴리스 낼까?", "릴리스 해", by="글"), id="typed-not-tapped"),
        pytest.param(_card("사이토플라즘 릴리스 낼까?", "릴리스 해", author="1" * 18, bot=False),
                     id="another-persons-card-shaped-message"),
        pytest.param(_card("사이토플라즘 릴리스 낼까?", "릴리스 해", webhook=True), id="webhook-card"),
        pytest.param(_card("사이토플라즘 릴리스 낼까?", "릴리스 해", folded=False), id="card-not-answered"),
        pytest.param(_card("사이토플라즘 릴리스 낼까?", "릴리스 해", seconds_ago=25 * 3600), id="card-older-than-a-day"),
    ],
)
def test_a_card_that_is_not_the_owners_release_tap_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, card: dict[str, object],
) -> None:
    discord = FakeDiscord(card)
    gate_dir = _gate(tmp_path, monkeypatch, discord)

    assert _command(_plan(tmp_path, card)) == 4

    assert not (gate_dir / "pending/release.json").exists()
    assert _authorize() == 2


def test_a_release_card_edited_after_the_record_is_not_an_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    discord = FakeDiscord(_card("사이토플라즘 릴리스 낼까?", "릴리스 해"))
    _ = _gate(tmp_path, monkeypatch, discord)
    assert _command(_plan(tmp_path, discord.message)) == 0

    discord.message = {**discord.message, "components": [{"type": 17, "components": [
        {"type": 10, "content": "✅ **Q37** 사이토플라즘 릴리스 낼까? v9도\n→ **릴리스 해**\n-# 버튼 · 11. 12. 15:04"}]}]}

    assert _authorize() == 1
    assert _decision() == 7


def test_one_owner_message_cannot_release_a_second_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    discord = FakeDiscord(_message("배포해"))
    gate_dir = _gate(tmp_path, monkeypatch, discord)
    assert _command(_plan(tmp_path, discord.message)) == 0
    (gate_dir / "pending/release.json").unlink()  # the first release was retired

    assert _command(_plan(tmp_path, discord.message, head="f" * 40)) == 4

    assert not (gate_dir / "pending/release.json").exists()


def test_rerunning_the_same_command_resumes_the_same_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    discord = FakeDiscord(_message("deploy please"))
    gate_dir = _gate(tmp_path, monkeypatch, discord)
    plan = _plan(tmp_path, discord.message)
    assert _command(plan) == 0
    before = (gate_dir / "pending/release.json").read_bytes()

    assert _command(plan) == 0

    assert (gate_dir / "pending/release.json").read_bytes() == before


def test_an_unreadable_owner_message_later_is_not_an_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    discord = FakeDiscord(_message("릴리즈 고"))
    _ = _gate(tmp_path, monkeypatch, discord)
    assert _command(_plan(tmp_path, discord.message)) == 0

    discord.fail = True

    assert _authorize() == 1
    assert _decision() == 7


def test_a_record_whose_message_no_longer_reads_as_the_owners_is_not_an_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    discord = FakeDiscord(_message("릴리스 해줘"))
    _ = _gate(tmp_path, monkeypatch, discord)
    assert _command(_plan(tmp_path, discord.message)) == 0

    discord.message = {**discord.message, "author": {"id": "1" * 18, "bot": False}}

    assert _authorize() == 1
    assert _decision() == 7


def test_a_tampered_record_is_not_an_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    discord = FakeDiscord(_message("릴리스"))
    gate_dir = _gate(tmp_path, monkeypatch, discord)
    assert _command(_plan(tmp_path, discord.message)) == 0
    record = _record(gate_dir)
    record["surface_digests"] = json.dumps([["skill:meeting", "e" * 64]])
    _ = (gate_dir / "pending/release.json").write_text(json.dumps(record), encoding="utf-8")

    assert _authorize(digest="e" * 64) == 1
