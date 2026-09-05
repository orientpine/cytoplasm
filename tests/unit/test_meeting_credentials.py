"""자식 프로세스 자격증명 — Codex OAuth 는 API 키가 아니라 HOME 으로 인증한다.

플러그인(`!meeting`)과 야간 배치(no-agent cron)는 둘 다 자식 프로세스를 띄운다. 자식의
모델 호출 경로는 Codex OAuth 하나뿐이므로 게이트웨이 API 키를 물려받을 이유가 없고,
대신 자격증명 저장소를 찾을 HOME 이 반드시 함께 가야 한다. 자격증명이 없으면 조용히
건너뛰거나 다른 티어로 내려가지 않고 눈에 보이게 실패한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from skills.meeting import plugin

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "meeting" / "scripts"))

import meeting_llm  # noqa: E402
import meeting_pending_transcript_watch as watcher  # noqa: E402

_DECOY_SECRETS = (
    "DISCORD_BOT_TOKEN=meeting-discord-token\n"
    "MODEL_GATEWAY_AGENT_KEY=must-not-be-forwarded\n"
    "MODEL_GATEWAY_BASE_URL=http://127.0.0.1:4000/v1\n"
)


def test_meeting_child_environment_forwards_codex_home_and_no_gateway_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    secrets_file = tmp_path / ".env.secrets"
    _ = secrets_file.write_text(_DECOY_SECRETS, encoding="utf-8")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MODEL_GATEWAY_AGENT_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(plugin, "ENV_SECRETS", secrets_file, raising=False)

    # When
    environment = plugin.child_environment()

    # Then
    assert environment["DISCORD_BOT_TOKEN"] == "meeting-discord-token"
    assert environment["HOME"] == str(tmp_path), "Codex 자격증명은 HOME 아래에서만 찾는다"
    assert [name for name in environment if name.startswith("MODEL_GATEWAY_")] == []
    assert plugin._CHILD_CREDENTIALS == frozenset({"DISCORD_BOT_TOKEN"})


def test_nightly_watcher_hands_the_child_home_instead_of_a_model_gateway_key(
    tmp_path: Path,
) -> None:
    """no-agent cron 도 같은 경로다 — 키를 넘기지 않고 HOME 만 넘긴다."""
    # Given
    _ = (tmp_path / ".env.secrets").write_text(_DECOY_SECRETS, encoding="utf-8")

    # When
    child = watcher.child_environment({"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})

    # Then
    assert child["HOME"] == str(tmp_path)
    assert child["DISCORD_BOT_TOKEN"] == "meeting-discord-token"
    assert [name for name in child if name.startswith("MODEL_GATEWAY_")] == []
    assert set(watcher.SECRET_KEYS) == {
        "OPENAI_API_KEY",
        "DISCORD_BOT_TOKEN",
        "DRIVE_PUBLISH_ENABLED",
        "DRIVE_GWS_BIN",
    }


def test_a_missing_codex_credential_fails_the_child_visibly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """자격증명이 없는 HOME 은 rc 1 + 빈 stdout 이다 — 그대로 예외로 올라와야 한다."""
    # Given
    hermes = tmp_path / "hermes"
    _ = hermes.write_text(
        "#!/bin/sh\n"
        'echo "hermes -z: agent failed: No Codex credentials stored." >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    hermes.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))

    # When
    with pytest.raises(meeting_llm.ExtractionUnavailableError) as failure:
        _ = meeting_llm.call_codex("회의 본문", sensitive=False)

    # Then
    assert "No Codex credentials stored" in str(failure.value)
    assert issubclass(meeting_llm.ExtractionUnavailableError, meeting_llm.ExtractionParseError), (
        "CLI 의 기존 실패 경로(exit 6)가 그대로 잡아야 조용한 성공으로 새지 않는다"
    )


def test_empty_trigger_looks_for_a_transcript_instead_of_refusing(monkeypatch) -> None:
    """`!meeting` 만 쓴 것은 오류가 아니라 "아직 회의록이 없는 전사본을 처리하라"는 요청이다."""
    spawned: list[list[str]] = []
    monkeypatch.setattr(plugin, "_spawn", spawned.append)

    plugin._launch(plugin.Trigger(chat_id="C1", doc_paths=(), body=None), "python3")
    plugin._launch(plugin.Trigger(chat_id="C1", doc_paths=(), body="   \n "), "python3")

    assert len(spawned) == 2, "본문이 공백뿐인 경우도 같은 요청이다"
    for argv in spawned:
        assert "--from-pending-transcript" in argv
        assert "--body-file" not in argv
