"""voice catalog ②b — 워처의 I/O 경계.

미러의 노트 한 줄이 enroll 자식 실행과 통지 한 건으로 이어지고, 같은 줄은 두 번째 틱에서
아무것도 하지 않는다. enroll 은 PATH 가 아니라 `VOICE_CATALOG_ENROLL_CLI` 로 가리킨 가짜
실행파일이라 단위 테스트가 오디오를 만지지 않고, 통지는 주입된 기록기가 봉투를 받는다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from automation.interop.owner_message import OwnerMessage
from automation.voice_catalog import enroll_watch, trigger

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "automation/voice_catalog/cron/voice_catalog_enroll_watch.py"
STEM = "2026-09-16_1115_자동_굴착"
NOTE_REL = f"000_PARA/Area/Lifelog/2026/{STEM}.md"


def _fake_cli(path: Path, *, fail: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "import json, os, sys\nfrom pathlib import Path\n"
        "receipt = Path(os.environ['HOME'], 'enroll-receipts.jsonl')\n"
        "with receipt.open('a') as fh:\n"
        "    fh.write(json.dumps({'argv': sys.argv[1:], 'secret': os.environ.get('FIXTURE_SECRET', '')}, ensure_ascii=False) + '\\n')\n"
    )
    if fail:
        body += "print('CATALOG-NO-SEGMENTS 화자7 — 그 화자의 1.5초 이상 블록이 없음', file=sys.stderr)\nsys.exit(2)\n"
    else:
        body += "print('noise line')\nprint(json.dumps({'name': sys.argv[sys.argv.index('--name') + 1], 'seconds': 42.5, 'wav': 'x.wav', 'recording_id': 'of_abc', 'intervals': 3}, ensure_ascii=False))\n"
    _ = path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path


def _home(tmp_path: Path, *, line: str = "- 화자:: 화자1=김민수", transcript: bool = True, fail: bool = False) -> tuple[Path, dict[str, str]]:
    home = tmp_path / "home"
    note = home / "mirror" / NOTE_REL
    note.parent.mkdir(parents=True)
    _ = note.write_text(f"---\ntitle: {STEM}\n---\n# {STEM}\n\n## 한눈에\n- 녹음:: 2026-09-16 (수) 11:15 · 20분 · 화자 4명\n{line}\n\n## 요약\n- 내용\n", encoding="utf-8")
    if transcript:
        transcripts = home / ".hermes/plaud-sync/transcripts"
        transcripts.mkdir(parents=True)
        _ = (transcripts / f"{STEM}.md").write_text("# 전사본\n\n- 원본 음성: of_abc.ogg\n\n---\n\n[00:00:00] 화자1\n안녕.\n", encoding="utf-8")
    cli = _fake_cli(home / "bin/fake_enroll.py", fail=fail)
    env = {
        "HOME": str(home),
        "VOICE_CATALOG_MIRROR": str(home / "mirror"),
        "VOICE_CATALOG_ENROLL_CLI": str(cli),
        "SPEECHTOTEXT_VOICE_CATALOG": str(home / "catalog"),
        "FIXTURE_SECRET": "fixture-value",
    }
    return home, env


class _Notifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, OwnerMessage]] = []

    def __call__(self, content: str, message: OwnerMessage) -> bool:
        self.sent.append((content, message))
        return True


def _receipts(home: Path) -> list[dict[str, object]]:
    path = home / "enroll-receipts.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _ledger(env: dict[str, str]) -> trigger.Ledger:
    return trigger.load_ledger((Path(env["SPEECHTOTEXT_VOICE_CATALOG"]) / trigger.LEDGER_FILE).read_text(encoding="utf-8"))


def test_a_new_speaker_line_enrolls_once_and_notifies_once(tmp_path: Path) -> None:
    home, env = _home(tmp_path)
    notifier = _Notifier()
    assert enroll_watch.run_once(env, home=home, notify=notifier) == 0
    receipts = _receipts(home)
    assert len(receipts) == 1
    argv = receipts[0]["argv"]
    assert argv == ["enroll", "--transcript", str(home / ".hermes/plaud-sync/transcripts" / f"{STEM}.md"), "--speaker", "화자1", "--name", "김민수"]
    assert receipts[0]["secret"] == "fixture-value"
    entry = _ledger(env)[NOTE_REL]["화자1"]
    assert entry.name == "김민수" and entry.status == "enrolled" and entry.reason == ""
    assert len(notifier.sent) == 1
    content, message = notifier.sent[0]
    assert "김민수" in content and "화자1" in content
    assert message.subject_key == f"voice-catalog/enroll/{STEM}/화자1" and "42.5" in message.fact
    ledger_file = Path(env["SPEECHTOTEXT_VOICE_CATALOG"]) / trigger.LEDGER_FILE
    assert (ledger_file.stat().st_mode & 0o777) == 0o600
    assert enroll_watch.run_once(env, home=home, notify=notifier) == 0
    assert len(_receipts(home)) == 1 and len(notifier.sent) == 1


def test_a_failed_enroll_is_marked_notified_once_and_retried_only_after_the_note_changes(tmp_path: Path) -> None:
    home, env = _home(tmp_path, line="- 화자:: 화자7=김민수", fail=True)
    notifier = _Notifier()
    assert enroll_watch.run_once(env, home=home, notify=notifier) == 0
    entry = _ledger(env)[NOTE_REL]["화자7"]
    assert entry.status == "failed" and entry.reason.startswith("CATALOG-NO-SEGMENTS 화자7")
    assert len(notifier.sent) == 1 and notifier.sent[0][1].subject_key == f"voice-catalog/enroll-failed/{STEM}/화자7"
    assert enroll_watch.run_once(env, home=home, notify=notifier) == 0
    assert len(_receipts(home)) == 1 and len(notifier.sent) == 1
    note = home / "mirror" / NOTE_REL
    _ = note.write_text(note.read_text(encoding="utf-8") + "\n- 메모:: 고쳤다\n", encoding="utf-8")
    assert enroll_watch.run_once(env, home=home, notify=notifier) == 0
    assert len(_receipts(home)) == 2 and len(notifier.sent) == 2


def test_a_missing_transcript_fails_closed_without_running_enroll(tmp_path: Path) -> None:
    home, env = _home(tmp_path, transcript=False)
    notifier = _Notifier()
    assert enroll_watch.run_once(env, home=home, notify=notifier) == 0
    assert _receipts(home) == []
    entry = _ledger(env)[NOTE_REL]["화자1"]
    assert entry.status == "failed" and entry.reason == "TRANSCRIPT-MISSING"
    assert len(notifier.sent) == 1


def test_notes_without_the_field_and_a_missing_mirror_do_nothing(tmp_path: Path, capsys) -> None:  # noqa: ANN001 - pytest fixture
    home, env = _home(tmp_path, line="- 사람:: [[김민수]]")
    notifier = _Notifier()
    assert enroll_watch.run_once(env, home=home, notify=notifier) == 0
    assert _receipts(home) == [] and notifier.sent == []
    assert not (Path(env["SPEECHTOTEXT_VOICE_CATALOG"]) / trigger.LEDGER_FILE).exists()
    env["VOICE_CATALOG_MIRROR"] = str(tmp_path / "nowhere")
    assert enroll_watch.run_once(env, home=home, notify=notifier) == 0
    assert "VOICE-CATALOG-SKIP reason=mirror-missing" in capsys.readouterr().out


def test_installed_wrapper_loads_secrets_and_forwards_them_to_the_enroll_child(tmp_path: Path) -> None:
    home, env = _home(tmp_path)
    installed = home / ".hermes/scripts/voice_catalog_enroll_watch.py"
    installed.parent.mkdir(parents=True)
    _ = installed.write_bytes(WRAPPER.read_bytes())
    secrets = "\n".join(f"{key}={value}" for key, value in env.items() if key != "HOME")
    _ = (home / ".env.secrets").write_text(secrets + f"\nAUTOPHAGY_RUNTIME_ROOT={REPO}\nDISCORD_BOT_TOKEN=\n", encoding="utf-8")
    child_env = {key: value for key, value in os.environ.items() if not key.startswith(("VOICE_CATALOG", "SPEECHTOTEXT", "AUTOPHAGY", "FIXTURE", "DISCORD"))}
    child_env["HOME"] = str(home)
    result = subprocess.run([sys.executable, str(installed), "--once"], cwd=tmp_path, env=child_env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert len(_receipts(home)) == 1 and _receipts(home)[0]["secret"] == "fixture-value"
    assert "VOICE-CATALOG-ENROLLED" in result.stdout
    assert "NOTIFY-UNCONFIGURED" in result.stderr
    assert ((home / "catalog").stat().st_mode & 0o777) == 0o700
