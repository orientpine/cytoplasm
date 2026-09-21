"""voice catalog ② — CLI 경계. propose 는 읽기 전용, enroll 은 배포 사본에서만 쓴다.

ffmpeg/ffprobe 는 PATH 앞의 가짜로 바꾼다 — 단위 테스트가 실제 오디오를 자르지 않고, 필터
문자열과 산출 경로가 argv 로 정확히 건너갔는지만 본다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "skills" / "speechtotext" / "scripts" / "stt_catalog_cli.py"

_TRANSCRIPT = "\n".join((
    "# 2026-09-07_1506_녹음 전사본",
    "",
    "- 원본 음성: of_abc.ogg",
    "- 화자: 화자1=미상 · 화자2=미상",
    "",
    "---",
    "",
    "[00:00:00] 화자1", "안녕하세요. 오늘 안건입니다.", "",
    "[00:00:20] 화자2", "네 시작하죠.", "",
    "[00:00:25] 화자1", "첫 번째는 일정입니다.", "",
    "[00:01:05] 화자2", "두 번째요.", "",
))


def _fake_tools(bin_dir: Path) -> None:
    bin_dir.mkdir()
    ffmpeg = bin_dir / "ffmpeg"
    ffmpeg.write_text(
        "#!/usr/bin/env python3\nimport sys, pathlib\n"
        "argv = sys.argv[1:]\n"
        "pathlib.Path(argv[-1]).write_bytes(b'RIFF' + b'\\0' * 60)\n"
        "pathlib.Path(argv[-1] + '.argv').write_text('\\n'.join(argv))\n",
        encoding="utf-8",
    )
    ffprobe = bin_dir / "ffprobe"
    ffprobe.write_text("#!/usr/bin/env python3\nprint('12.500000')\n", encoding="utf-8")
    ffmpeg.chmod(0o755)
    ffprobe.chmod(0o755)


def _run(
    tmp_path: Path, *argv: str, live_skill: bool = False, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    live = tmp_path / "live"
    if live_skill:
        (live / "speechtotext" / "scripts").mkdir(parents=True, exist_ok=True)
    tools = tmp_path / "bin"
    if not tools.exists():
        _fake_tools(tools)
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "SPEECHTOTEXT_VOICE_CATALOG": str(tmp_path / "catalog"),
        "AUTOPHAGY_SKILL_LIVE_ROOT": str(live),
        "AUTOPHAGY_REPO_ROOT": str(REPO),
    }
    return subprocess.run(
        [sys.executable, str(CLI), *argv], capture_output=True, text=True,
        env={**env, **(extra or {})}, check=False,
    )


def _transcript(tmp_path: Path) -> Path:
    path = tmp_path / "2026-09-07-150643--13b66f70f19d.md"
    path.write_text(_TRANSCRIPT, encoding="utf-8")
    return path


def test_propose_reports_each_speaker_and_writes_nothing(tmp_path: Path) -> None:
    result = _run(tmp_path, "propose", str(_transcript(tmp_path)), "--duration-ms", "80000")
    assert result.returncode == 0, result.stderr
    rows = {row["label"]: row for row in json.loads(result.stdout.splitlines()[-1])["speakers"]}
    assert rows["화자1"] == {"label": "화자1", "blocks": 2, "speech_ms": 60000,
                             "planned_ms": 50000, "intervals": [[0, 20000], [25000, 55000]]}
    assert rows["화자2"]["blocks"] == 2 and rows["화자2"]["speech_ms"] == 20000
    assert not (tmp_path / "catalog").exists()


def test_enroll_cuts_with_the_planned_filter_and_is_idempotent(tmp_path: Path) -> None:
    audio = tmp_path / "rec.ogg"
    audio.write_bytes(b"x" * 10)
    argv = ("enroll", "--transcript", str(_transcript(tmp_path)), "--speaker", "화자1",
            "--name", "홍길동", "--audio", str(audio))
    first = _run(tmp_path, *argv)
    assert first.returncode == 0, first.stderr
    done = json.loads(first.stdout.splitlines()[-1])
    assert done["name"] == "홍길동" and done["seconds"] == 12.5
    wav = tmp_path / "catalog" / "samples" / "홍길동-of_abc-화자1.wav"
    assert wav.is_file() and (wav.stat().st_mode & 0o777) == 0o600
    passed = (tmp_path / "catalog" / "samples" / (wav.name + ".argv")).read_text().splitlines()
    assert "-filter_complex" in passed
    assert passed[passed.index("-filter_complex") + 1].startswith("[0:a]atrim=start=0:end=20,")
    assert passed[-1] == str(wav)
    catalog = json.loads((tmp_path / "catalog" / "catalog.json").read_text())
    assert [person["name"] for person in catalog["people"]] == ["홍길동"]
    second = _run(tmp_path, *argv)
    assert second.returncode == 0, second.stderr
    catalog = json.loads((tmp_path / "catalog" / "catalog.json").read_text())
    assert len(catalog["people"][0]["samples"]) == 1
    assert catalog["people"][0]["samples"][0]["recording_id"] == "of_abc"


def test_enroll_refuses_a_label_without_segments_and_writes_nothing(tmp_path: Path) -> None:
    audio = tmp_path / "rec.ogg"
    audio.write_bytes(b"x")
    result = _run(tmp_path, "enroll", "--transcript", str(_transcript(tmp_path)),
                  "--speaker", "화자7", "--name", "홍길동", "--audio", str(audio))
    assert result.returncode == 2
    assert "CATALOG-NO-SEGMENTS 화자7" in result.stderr
    assert not (tmp_path / "catalog").exists()


def test_enroll_without_audio_or_manifest_row_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path, "enroll", "--transcript", str(_transcript(tmp_path)),
                  "--speaker", "화자1", "--name", "홍길동")
    assert result.returncode == 2
    assert "CATALOG-AUDIO-MISSING" in result.stderr
    assert not (tmp_path / "catalog").exists()


def test_list_and_remove_round_trip(tmp_path: Path) -> None:
    audio = tmp_path / "rec.ogg"
    audio.write_bytes(b"x")
    _ = _run(tmp_path, "enroll", "--transcript", str(_transcript(tmp_path)), "--speaker", "화자1",
             "--name", "홍길동", "--audio", str(audio))
    listed = _run(tmp_path, "list")
    assert listed.returncode == 0 and "홍길동" in listed.stdout
    removed = _run(tmp_path, "remove", "--name", "홍길동")
    assert removed.returncode == 0
    assert not list((tmp_path / "catalog" / "samples").glob("*.wav"))
    assert json.loads((tmp_path / "catalog" / "catalog.json").read_text())["people"] == []
    assert _run(tmp_path, "remove", "--name", "홍길동").returncode == 2


def test_mutating_subcommands_refuse_a_stale_copy_when_a_live_mount_exists(tmp_path: Path) -> None:
    audio = tmp_path / "rec.ogg"
    audio.write_bytes(b"x")
    result = _run(tmp_path, "enroll", "--transcript", str(_transcript(tmp_path)), "--speaker",
                  "화자1", "--name", "홍길동", "--audio", str(audio), live_skill=True)
    assert result.returncode == 3
    assert "STALE-SKILL-COPY-BLOCK" in result.stderr
    assert _run(tmp_path, "propose", str(_transcript(tmp_path)), live_skill=True).returncode == 0

def _fake_voiceprint(path: Path) -> None:
    """임베딩 자식 대역 — 등록본과 화자1 은 같은 방향, 나머지는 직교."""
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "with open(sys.argv[sys.argv.index('--job') + 1], encoding='utf-8') as handle:\n"
        "    job = json.load(handle)\n"
        "vectors = {}\n"
        "for one in job['jobs']:\n"
        "    key = str(one['id'])\n"
        "    base = key.split('#', 1)[0]\n"
        "    same = base.startswith('등록:') or base == '화자1'\n"
        "    vectors[key] = [1.0, 0.0] if same else [0.0, 1.0]\n"
        "print(json.dumps({'dim': 2, 'model_sha256': 'e' * 64,\n"
        "                  'embeddings': vectors, 'failed': {}}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _sherpa(tmp_path: Path) -> dict[str, str]:
    binary = tmp_path / "sherpa" / "bin" / "diarize"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"")
    library = tmp_path / "sherpa" / "lib"
    library.mkdir(parents=True, exist_ok=True)
    (library / "libsherpa-onnx-c-api.so").write_bytes(b"")
    model = tmp_path / "sherpa" / "models" / "embedding.onnx"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"onnx")
    voiceprint = tmp_path / "fake_voiceprint.py"
    _fake_voiceprint(voiceprint)
    return {
        "SPEECHTOTEXT_DIARIZE_BIN": str(binary),
        "SPEECHTOTEXT_DIARIZE_EMBEDDING": str(model),
        "SPEECHTOTEXT_VOICEPRINT_CLI": str(voiceprint),
    }


def test_match_reports_the_verdict_for_each_label_and_writes_nothing(tmp_path: Path) -> None:
    audio = tmp_path / "rec.ogg"
    audio.write_bytes(b"x" * 10)
    transcript = _transcript(tmp_path)
    enrolled = _run(
        tmp_path, "enroll", "--transcript", str(transcript), "--speaker", "화자1",
        "--name", "홍길동", "--audio", str(audio),
    )
    assert enrolled.returncode == 0, enrolled.stderr
    before = (tmp_path / "catalog" / "catalog.json").read_bytes()

    result = _run(tmp_path, "match", str(transcript), "--audio", str(audio), extra=_sherpa(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "화자1: 홍길동 1.00" in result.stdout
    assert "→ 확정" in result.stdout
    payload = json.loads(result.stdout.splitlines()[-1])
    verdicts = {row["label"]: row for row in payload["verdicts"]}
    assert verdicts["화자1"]["kind"] == "확정" and verdicts["화자1"]["name"] == "홍길동"
    assert verdicts["화자2"]["kind"] == "미상"
    assert payload["scores"]["화자2"]["홍길동"] == 0.0
    assert (tmp_path / "catalog" / "catalog.json").read_bytes() == before


def test_match_says_why_it_cannot_compare_without_the_c_api(tmp_path: Path) -> None:
    audio = tmp_path / "rec.ogg"
    audio.write_bytes(b"x" * 10)
    transcript = _transcript(tmp_path)
    enrolled = _run(
        tmp_path, "enroll", "--transcript", str(transcript), "--speaker", "화자1",
        "--name", "홍길동", "--audio", str(audio),
    )
    assert enrolled.returncode == 0, enrolled.stderr

    result = _run(tmp_path, "match", str(transcript), "--audio", str(audio))

    assert result.returncode == 0, result.stderr
    assert "IDENTIFY-SKIP reason=no-toolchain" in result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["verdicts"] == []
