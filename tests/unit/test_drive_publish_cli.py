"""An agent-authored deliverable needs a sanctioned command, not a local path.

2026-08-26: a 공정표 template built for eight external institutions was left in
~/Documents because every caller of the publishing facade was skill code — there was
no command a session could run. A rule with no command behind it is not followed, so
the rule and this entry point ship together.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from automation import drive_publish_cli


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    path = tmp_path / "산출물.xlsx"
    path.write_bytes(b"xlsx-bytes")
    return path


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_publish(kind, title, artifacts, *, companions=(), on=None, project=None, client=None):
        calls.append({
            "kind": kind, "title": title, "artifacts": list(artifacts),
            "companions": list(companions), "on": on, "project": project,
        })
        return "published"

    monkeypatch.setattr(drive_publish_cli, "publish", fake_publish)
    return calls


def test_refuses_an_unknown_kind(artifact, recorder, monkeypatch):
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    code = drive_publish_cli.main(
        ["--kind", "존재하지않는종류", "--title", "t", str(artifact)]
    )
    assert code != 0
    assert recorder == []


def test_refuses_a_gate_only_kind(artifact, recorder, monkeypatch):
    """patent is gate-only — it leaves through its own export gate, never here."""
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    code = drive_publish_cli.main(["--kind", "patent", "--title", "t", str(artifact)])
    assert code != 0
    assert recorder == []


def test_refuses_a_missing_file_before_touching_drive(tmp_path, recorder, monkeypatch):
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    code = drive_publish_cli.main(
        ["--kind", "doctype", "--title", "t", str(tmp_path / "없는파일.xlsx")]
    )
    assert code != 0
    assert recorder == []


def test_makes_no_drive_call_when_publishing_is_not_enabled(artifact, recorder, monkeypatch, capsys):
    """Silence would let a caller believe it published. Say so, and fail."""
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    code = drive_publish_cli.main(["--kind", "doctype", "--title", "t", str(artifact)])
    assert code != 0
    assert recorder == []
    assert "DRIVE-PUBLISH-DISABLED" in capsys.readouterr().err


def test_forwards_every_parsed_argument_to_the_facade(artifact, recorder, monkeypatch):
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    code = drive_publish_cli.main([
        "--kind", "doctype",
        "--title", "용역공정표-템플릿",
        "--project", "해양고신뢰성",
        "--on", "2026-08-26",
        str(artifact),
    ])

    assert code == 0
    assert len(recorder) == 1
    call = recorder[0]
    assert call["kind"] == "doctype"
    assert call["title"] == "용역공정표-템플릿"
    assert call["project"] == "해양고신뢰성"
    assert call["on"] == date(2026, 8, 26)
    assert call["artifacts"] == [(artifact, "용역공정표-템플릿")]


@pytest.mark.parametrize(
    ("kind", "command"),
    [("meeting", "meeting_cli.py ingest"), ("transcript", "speechtotext_cli.py")],
)
def test_refuses_a_kind_that_a_skill_owns(kind, command, artifact, recorder, monkeypatch, capsys):
    """손으로 쓴 문서를 회의록으로 발행하면 원장을 거치지 않아 관리번호가 존재할 수 없다.

    2026-08-27 실측: 에이전트가 전사본을 읽어 회의록을 직접 쓰고 `--kind meeting` 으로
    발행했다. 스킬은 한 번도 돌지 않았고(그날 인제스트 로그 0건), action item 번호는
    `TR-260825-M01` 처럼 그 자리에서 지어낸 것이 실렸다. 발행 경로가 두 개면 원장은
    한쪽만 본다.
    """
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")

    code = drive_publish_cli.main(["--kind", kind, "--title", "t", str(artifact)])

    assert code == drive_publish_cli.EXIT_REFUSED
    assert recorder == [], "Drive 를 건드리기 전에 거부해야 한다"
    assert command in capsys.readouterr().err, "올바른 명령을 알려주지 않으면 다시 손으로 쓴다"


def test_kinds_no_skill_owns_stay_hand_publishable(artifact, recorder, monkeypatch):
    """가드는 회의록·전사본에만 걸린다 — 세션이 손으로 내는 산출물 경로는 그대로다."""
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")

    assert drive_publish_cli.main(["--kind", "doctype", "--title", "t", str(artifact)]) == 0
    assert len(recorder) == 1
