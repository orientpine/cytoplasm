"""speechtotext skill — Drive audio -> transcript(.md) -> meeting minutes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "speechtotext"
sys.path.insert(0, str(SKILL / "scripts"))

import stt_audio  # noqa: E402


def _write(path: Path, size: int) -> Path:
    path.write_bytes(b"\0" * size)
    return path


def test_accepts_supported_audio_extension(tmp_path: Path) -> None:
    checked = stt_audio.check_audio(_write(tmp_path / "a.m4a", 1024))
    assert checked.suffix == ".m4a"
    assert checked.size_bytes == 1024
    assert checked.mime == "audio/mp4"


def test_rejects_unsupported_extension_with_exit_5(tmp_path: Path) -> None:
    with pytest.raises(stt_audio.TranscriptionRefused) as caught:
        stt_audio.check_audio(_write(tmp_path / "notes.txt", 10))
    assert caught.value.exit_code == 5
    assert "지원하지 않는" in caught.value.notice


def test_rejects_oversized_file_before_reading_bytes(tmp_path: Path, monkeypatch) -> None:
    target = _write(tmp_path / "big.wav", 16)
    monkeypatch.setattr(stt_audio, "MAX_AUDIO_BYTES", 8)

    def explode(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("oversized input must be refused before any content read")

    monkeypatch.setattr(Path, "read_bytes", explode)
    with pytest.raises(stt_audio.TranscriptionRefused) as caught:
        stt_audio.check_audio(target)
    assert caught.value.exit_code == 3


def test_rejects_missing_file_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(stt_audio.TranscriptionRefused) as caught:
        stt_audio.check_audio(tmp_path / "absent.mp3")
    assert caught.value.exit_code == 5


def test_supported_suffixes_match_provider_contract() -> None:
    assert stt_audio.SUPPORTED_SUFFIXES == frozenset(
        {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
    )


# --- transcription client: proven against a real local HTTP server -----------

import json  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

import stt_client  # noqa: E402

API_KEY = "test-key-not-a-real-credential"


def _split_multipart(body: bytes, boundary: str) -> dict[str, tuple[dict[str, str], bytes]]:
    """Minimal multipart/form-data reader used only to assert what we really sent."""
    marker = b"--" + boundary.encode("ascii")
    parts: dict[str, tuple[dict[str, str], bytes]] = {}
    for chunk in body.split(marker):
        if not chunk.strip(b"-\r\n"):
            continue
        head, _, payload = chunk.lstrip(b"\r\n").partition(b"\r\n\r\n")
        headers: dict[str, str] = {}
        for line in head.decode("utf-8").splitlines():
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        disposition = headers.get("content-disposition", "")
        name = disposition.split('name="', 1)[1].split('"', 1)[0]
        parts[name] = (headers, payload[: -2] if payload.endswith(b"\r\n") else payload)
    return parts


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.status = 200
        self.body = json.dumps({"text": "안녕하세요. 킥오프 회의를 시작합니다."}).encode("utf-8")


@pytest.fixture
def stt_server():
    recorder = _Recorder()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            boundary = self.headers.get("Content-Type", "").split("boundary=", 1)[-1]
            recorder.calls.append({
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "content_type": self.headers.get("Content-Type", ""),
                "parts": _split_multipart(raw, boundary),
            })
            self.send_response(recorder.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(recorder.body)))
            self.end_headers()
            self.wfile.write(recorder.body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    recorder.base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        yield recorder
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_transcribe_posts_real_multipart_over_http(tmp_path: Path, stt_server) -> None:
    audio_bytes = (SKILL / "fixtures" / "sample-meeting.wav").read_bytes()
    source = tmp_path / "회의녹음.wav"
    source.write_bytes(audio_bytes)

    result = stt_client.transcribe(
        stt_audio.check_audio(source),
        api_key=API_KEY,
        base_url=stt_server.base_url,
        model="gpt-4o-transcribe",
        language="ko",
    )

    assert result.text == "안녕하세요. 킥오프 회의를 시작합니다."
    assert result.model == "gpt-4o-transcribe"
    call = stt_server.calls[0]
    assert call["path"] == "/v1/audio/transcriptions"
    assert call["authorization"] == f"Bearer {API_KEY}"
    assert call["content_type"].startswith("multipart/form-data; boundary=")
    parts = call["parts"]
    assert parts["model"][1] == b"gpt-4o-transcribe"
    assert parts["language"][1] == b"ko"
    assert parts["response_format"][1] == b"json"
    assert 'filename="회의녹음.wav"' in parts["file"][0]["content-disposition"]
    assert parts["file"][0]["content-type"] == "audio/wav"
    assert parts["file"][1] == audio_bytes


def test_transcribe_refuses_without_api_key(tmp_path: Path, stt_server) -> None:
    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFFxxxx")
    with pytest.raises(stt_client.SttError) as caught:
        stt_client.transcribe(
            stt_audio.check_audio(source), api_key="", base_url=stt_server.base_url,
            model="gpt-4o-transcribe", language="ko",
        )
    assert caught.value.exit_code == 6
    assert stt_server.calls == []


def test_transcribe_maps_http_error_without_leaking_the_key(tmp_path: Path, stt_server) -> None:
    stt_server.status = 401
    stt_server.body = json.dumps(
        {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}}
    ).encode("utf-8")
    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFFxxxx")
    with pytest.raises(stt_client.SttError) as caught:
        stt_client.transcribe(
            stt_audio.check_audio(source), api_key=API_KEY, base_url=stt_server.base_url,
            model="gpt-4o-transcribe", language="ko",
        )
    message = str(caught.value)
    assert "401" in message
    assert "Incorrect API key provided" in message
    assert API_KEY not in message


def test_transcribe_refuses_blank_transcript(tmp_path: Path, stt_server) -> None:
    stt_server.body = json.dumps({"text": "   \n  "}).encode("utf-8")
    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFFxxxx")
    with pytest.raises(stt_audio.TranscriptionRefused) as caught:
        stt_client.transcribe(
            stt_audio.check_audio(source), api_key=API_KEY, base_url=stt_server.base_url,
            model="gpt-4o-transcribe", language="ko",
        )
    assert caught.value.exit_code == 5


def test_build_multipart_is_binary_safe() -> None:
    payload = b"\r\n--boundary-lookalike\r\n\xff\x00\x1b"
    body, content_type = stt_client.build_multipart(
        {"model": "whisper-1"}, filename="x.wav", mime="audio/wav", payload=payload
    )
    boundary = content_type.split("boundary=", 1)[1]
    assert body.count(payload) == 1
    assert body.endswith(f"--{boundary}--\r\n".encode("ascii"))


# --- transcript rendering + the real subprocess chain into `meeting` ---------

import os  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import speechtotext_cli  # noqa: E402
import stt_transcript  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 8, 25, 14, 53, tzinfo=KST)
SPOKEN = "오늘 킥오프에서 일정과 담당을 정했습니다. 다음 주까지 초안을 공유하기로 했습니다."


def _transcription(text: str = SPOKEN) -> stt_client.Transcription:
    return stt_client.Transcription(
        text=text, model="gpt-4o-transcribe", endpoint="https://api.openai.com/v1/audio/transcriptions"
    )


def test_transcript_name_is_date_prefixed_and_sanitized() -> None:
    assert stt_transcript.transcript_name("킥오프 / 1차", NOW) == "2026-08-25_킥오프-1차.md"


def test_transcript_file_and_directory_are_owner_only(tmp_path: Path) -> None:
    written = stt_transcript.write_transcript(
        tmp_path / "transcripts", label="킥오프", source_name="회의녹음.wav",
        transcription=_transcription(), now=NOW,
    )
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert stat.S_IMODE(written.parent.stat().st_mode) == 0o700


def test_transcript_body_carries_provenance_and_full_text(tmp_path: Path) -> None:
    written = stt_transcript.write_transcript(
        tmp_path / "t", label="킥오프", source_name="회의녹음.wav",
        transcription=_transcription(), now=NOW,
    )
    body = written.read_text(encoding="utf-8")
    assert body.startswith("# 킥오프 전사본")
    assert "회의녹음.wav" in body
    assert "gpt-4o-transcribe" in body
    assert "2026-08-25" in body
    assert SPOKEN in body


def test_rewriting_the_same_label_updates_one_file(tmp_path: Path) -> None:
    directory = tmp_path / "t"
    first = stt_transcript.write_transcript(
        directory, label="킥오프", source_name="a.wav", transcription=_transcription(), now=NOW
    )
    second = stt_transcript.write_transcript(
        directory, label="킥오프", source_name="a.wav",
        transcription=_transcription("다시 전사한 내용입니다."), now=NOW,
    )
    assert first == second
    assert list(directory.iterdir()) == [first]
    assert "다시 전사한 내용입니다." in second.read_text(encoding="utf-8")


@pytest.fixture
def fake_meeting(tmp_path: Path) -> tuple[Path, Path]:
    """A stand-in meeting CLI that records the real argv and env it was given."""
    record = tmp_path / "meeting-call.json"
    script = tmp_path / "fake_meeting_cli.py"
    script.write_text(
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'env': dict(os.environ)}}, open({str(record)!r}, 'w'))\n"
        "sys.exit(int(os.environ.get('FAKE_MEETING_EXIT', '0')))\n",
        encoding="utf-8",
    )
    return script, record


def _cli_env(tmp_path: Path, monkeypatch, fake_meeting: tuple[Path, Path]) -> None:
    # main() loads ~/.env.secrets into the environment, so every CLI test runs on its own
    # copy — a fixture credential must not survive into the next test.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setenv("SPEECHTOTEXT_TRANSCRIPT_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setenv("SPEECHTOTEXT_MEETING_CLI", str(fake_meeting[0]))
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)


def test_ingest_chains_transcript_into_meeting_with_credentials(
    tmp_path: Path, monkeypatch, fake_meeting, capsys
) -> None:
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    # The credential exists only in ~/.env.secrets — never in this process's environ.
    (tmp_path / "home" / ".env.secrets").write_text(
        "OPENAI_API_KEY=stt-key-fixture\n", encoding="utf-8"
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    audio = tmp_path / "회의녹음.wav"
    audio.write_bytes((SKILL / "fixtures" / "sample-meeting.wav").read_bytes())
    spoken = tmp_path / "spoken.txt"
    spoken.write_text(SPOKEN, encoding="utf-8")

    exit_code = speechtotext_cli.main(
        ["ingest", "--file", str(audio), "--label", "킥오프", "--recorded", str(spoken)]
    )

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    transcript = Path(summary["transcript_path"])
    assert transcript.is_file()
    # 전사본은 문장마다 한 줄이므로 말한 문장이 통째로 살아 있는지로 확인한다.
    written = transcript.read_text(encoding="utf-8")
    for sentence in stt_polish.split_sentences(SPOKEN):
        assert f"\n{sentence}\n" in written
    assert summary["meeting_exit"] == 0

    call = json.loads(fake_meeting[1].read_text(encoding="utf-8"))
    assert call["argv"][0] == "ingest"
    assert call["argv"][call["argv"].index("--file") + 1] == str(transcript)
    assert call["argv"][call["argv"].index("--label") + 1] == "킥오프"
    assert call["env"]["OPENAI_API_KEY"] == "stt-key-fixture"
    # The retired gateway credentials are gone from both child forward lists: what the
    # child is handed explicitly is exactly the transcription and Discord credentials.
    assert stt_runtime._SECRET_KEYS == ("OPENAI_API_KEY", "DISCORD_BOT_TOKEN")
    assert watcher._SECRET_KEYS == stt_runtime._SECRET_KEYS


def test_ingest_refuses_unsupported_audio_without_calling_meeting(
    tmp_path: Path, monkeypatch, fake_meeting, capsys
) -> None:
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    source = tmp_path / "notes.txt"
    source.write_text("텍스트 파일", encoding="utf-8")
    assert speechtotext_cli.main(["ingest", "--file", str(source), "--label", "x"]) == 5
    assert not fake_meeting[1].exists()
    assert "지원하지 않는" in capsys.readouterr().out


def test_ingest_reports_meeting_failure_as_exit_7(
    tmp_path: Path, monkeypatch, fake_meeting, capsys
) -> None:
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    monkeypatch.setenv("FAKE_MEETING_EXIT", "6")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF0000")
    spoken = tmp_path / "spoken.txt"
    spoken.write_text(SPOKEN, encoding="utf-8")
    assert speechtotext_cli.main(
        ["ingest", "--file", str(audio), "--label", "킥오프", "--recorded", str(spoken)]
    ) == 7
    # the transcript survives a failed chain so a retry never re-pays for transcription
    assert list((tmp_path / "transcripts").glob("*.md"))


def test_transcribe_verb_stops_before_meeting(
    tmp_path: Path, monkeypatch, fake_meeting, capsys
) -> None:
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF0000")
    spoken = tmp_path / "spoken.txt"
    spoken.write_text(SPOKEN, encoding="utf-8")
    assert speechtotext_cli.main(
        ["transcribe", "--file", str(audio), "--label", "킥오프", "--recorded", str(spoken)]
    ) == 0
    assert not fake_meeting[1].exists()
    assert json.loads(capsys.readouterr().out)["meeting_exit"] is None


# --- local whisper.cpp backend: audio never leaves the node ------------------

import stt_local  # noqa: E402

_WHISPER_JSON = (
    '{"transcription":[{"text":" 오늘 킥오프 회의입니다."},'
    '{"text":" 다음 주까지 초안을 공유합니다."}]}'
)


@pytest.fixture
def local_toolchain(tmp_path: Path):
    """Real executable stand-ins for ffmpeg and whisper-cli (genuine subprocesses)."""
    binary = tmp_path / "whisper-cli"
    binary.write_text(
        "#!/bin/sh\n"
        'of=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -of) of="$2"; shift 2;; *) shift;; esac; done\n'
        "printf '%s' '" + _WHISPER_JSON + "' > \"$of.json\"\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text(
        '#!/bin/sh\nfor a in "$@"; do last="$a"; done\nprintf "RIFF" > "$last"\n', encoding="utf-8"
    )
    ffmpeg.chmod(0o755)
    model = tmp_path / "ggml-large-v3-turbo-q5_0.bin"
    model.write_bytes(b"ggml-model-fixture")
    return binary, ffmpeg, model


def _local_env(monkeypatch, local_toolchain) -> None:
    binary, ffmpeg, model = local_toolchain
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_BIN", str(binary))
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_MODEL", str(model))
    monkeypatch.setenv("SPEECHTOTEXT_FFMPEG_BIN", str(ffmpeg))
    # The window cache holds transcribed speech; a test must never write it into the
    # owner's real ~/.hermes tree.
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_CACHE", str(binary.parent / "window-cache"))


def test_resolve_toolchain_is_none_when_binary_or_model_is_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_BIN", str(tmp_path / "absent-whisper"))
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_MODEL", str(tmp_path / "absent.bin"))
    assert stt_local.resolve_toolchain(dict(os.environ)) is None


def test_local_backend_converts_to_16k_mono_then_runs_whisper(
    tmp_path, monkeypatch, local_toolchain
) -> None:
    _local_env(monkeypatch, local_toolchain)
    recorded: list[list[str]] = []
    real_run = subprocess.run

    def spy(argv, **kwargs):
        recorded.append(list(argv))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(stt_local.subprocess, "run", spy)
    audio = tmp_path / "회의.m4a"
    audio.write_bytes(b"fake-m4a")
    toolchain = stt_local.resolve_toolchain(dict(os.environ))

    result = stt_local.transcribe(stt_audio.check_audio(audio), toolchain)

    convert, transcribe_argv = recorded[0], recorded[1]
    assert convert[0] == str(local_toolchain[1])
    assert convert[convert.index("-ar") + 1] == "16000"
    assert convert[convert.index("-ac") + 1] == "1"
    assert convert[convert.index("-c:a") + 1] == "pcm_s16le"
    assert transcribe_argv[0] == str(local_toolchain[0])
    assert transcribe_argv[transcribe_argv.index("-m") + 1] == str(local_toolchain[2])
    assert transcribe_argv[transcribe_argv.index("-l") + 1] == "ko"
    assert "-ojf" in transcribe_argv and "-np" in transcribe_argv
    assert result.text == "오늘 킥오프 회의입니다. 다음 주까지 초안을 공유합니다."


def test_local_backend_reports_offline_model_provenance(
    tmp_path, monkeypatch, local_toolchain
) -> None:
    _local_env(monkeypatch, local_toolchain)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    result = stt_local.transcribe(
        stt_audio.check_audio(audio), stt_local.resolve_toolchain(dict(os.environ))
    )
    assert result.model.startswith("local:")
    assert "ggml-large-v3-turbo-q5_0" in result.model
    assert result.endpoint == "local"


def test_local_backend_refuses_blank_transcription(
    tmp_path, monkeypatch, local_toolchain
) -> None:
    _local_env(monkeypatch, local_toolchain)
    binary = local_toolchain[0]
    binary.write_text(
        "#!/bin/sh\n"
        'of=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -of) of="$2"; shift 2;; *) shift;; esac; done\n'
        'printf \'{"transcription":[{"text":"  "}]}\' > "$of.json"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    with pytest.raises(stt_audio.TranscriptionRefused) as caught:
        stt_local.transcribe(
            stt_audio.check_audio(audio), stt_local.resolve_toolchain(dict(os.environ))
        )
    assert caught.value.exit_code == 5


def test_explicit_local_backend_never_falls_back_to_the_network(
    tmp_path, monkeypatch, fake_meeting, capsys
) -> None:
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    monkeypatch.setenv("SPEECHTOTEXT_BACKEND", "local")
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_BIN", str(tmp_path / "absent-whisper"))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("explicit local backend must never send audio to a provider")

    monkeypatch.setattr(speechtotext_cli.stt_client, "transcribe", forbidden)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    assert speechtotext_cli.main(["ingest", "--file", str(audio), "--label", "비공개"]) == 4
    assert not fake_meeting[1].exists()
    assert "로컬" in capsys.readouterr().out


def test_auto_backend_prefers_the_local_toolchain_when_present(
    tmp_path, monkeypatch, fake_meeting, local_toolchain, capsys
) -> None:
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    _local_env(monkeypatch, local_toolchain)
    monkeypatch.setenv("SPEECHTOTEXT_BACKEND", "auto")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("auto must prefer the local toolchain when it resolves")

    monkeypatch.setattr(speechtotext_cli.stt_client, "transcribe", forbidden)
    audio = tmp_path / "회의.wav"
    audio.write_bytes(b"RIFF")

    assert speechtotext_cli.main(["ingest", "--file", str(audio), "--label", "킥오프"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["model"].startswith("local:")
    assert "오늘 킥오프 회의입니다." in Path(summary["transcript_path"]).read_text(encoding="utf-8")


# --- Drive watcher: read-only polling, idempotent, credentials to the child --

from datetime import UTC  # noqa: E402

import speechtotext_drive_watch as watcher  # noqa: E402
import stt_drive  # noqa: E402

WATCH_NOW = datetime(2026, 8, 25, 6, 0, tzinfo=UTC)


class _FakeDrive:
    def __init__(self, children: list[dict[str, str]], shared: tuple[str, ...] = ()) -> None:
        self.children = children
        self.shared = shared
        self.resolved: list[tuple[str, ...]] = []
        self.downloaded: list[str] = []

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str:
        self.resolved.append(parts)
        return "folder-1"

    def list_children(self, folder_id: str) -> list[dict[str, str]]:
        assert folder_id == "folder-1"
        return list(self.children)

    def verify_owner_only(self, file_id: str) -> None:
        if file_id in self.shared:
            raise RuntimeError(f"소유자 권한이 유일하지 않음: {file_id}")

    def download_file(self, file_id: str, dest: Path) -> str:
        self.downloaded.append(file_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"audio-bytes")
        return "sha-" + file_id


def _runner_recording(calls: list[tuple[list[str], dict[str, str]]], code: int = 0):
    def runner(argv: list[str], env: dict[str, str]) -> int:
        calls.append((argv, env))
        return code

    return runner


def _watch_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        "HOME": str(tmp_path / "home"),
        "SPEECHTOTEXT_DRIVE_FOLDER": "회의녹음",
        "SPEECHTOTEXT_STATE_FILE": str(tmp_path / "state.json"),
    }
    env.update(extra)
    (tmp_path / "home").mkdir(exist_ok=True)
    return env


def test_watch_refuses_when_no_folder_is_configured(tmp_path: Path) -> None:
    env = _watch_env(tmp_path)
    del env["SPEECHTOTEXT_DRIVE_FOLDER"]
    drive = _FakeDrive([])
    calls: list[tuple[list[str], dict[str, str]]] = []
    with pytest.raises(stt_drive.DriveScanRefused) as caught:
        watcher.run_once(client=drive, env=env, runner=_runner_recording(calls), now=WATCH_NOW)
    assert caught.value.exit_code == 4
    assert drive.resolved == [] and calls == []


def test_watch_ingests_only_new_audio_and_marks_after_success(tmp_path: Path) -> None:
    env = _watch_env(tmp_path)
    drive = _FakeDrive([
        {"id": "f-old", "name": "지난주.m4a"},
        {"id": "f-new", "name": "킥오프 회의.m4a"},
        {"id": "f-doc", "name": "메모.txt"},
    ])
    Path(env["SPEECHTOTEXT_STATE_FILE"]).write_text(
        json.dumps({"processed": {"f-old": {"name": "지난주.m4a"}}}), encoding="utf-8"
    )
    calls: list[tuple[list[str], dict[str, str]]] = []

    summary = watcher.run_once(
        client=drive, env=env, runner=_runner_recording(calls), now=WATCH_NOW
    )

    assert summary["ingested"] == 1
    assert drive.downloaded == ["f-new"]
    assert len(calls) == 1
    argv = calls[0][0]
    assert argv[argv.index("--label") + 1] == "킥오프 회의"
    state = json.loads(Path(env["SPEECHTOTEXT_STATE_FILE"]).read_text(encoding="utf-8"))
    assert set(state["processed"]) == {"f-old", "f-new"}


def test_watch_does_not_mark_a_file_whose_ingest_failed(tmp_path: Path) -> None:
    env = _watch_env(tmp_path)
    drive = _FakeDrive([{"id": "f-new", "name": "킥오프.m4a"}])
    calls: list[tuple[list[str], dict[str, str]]] = []

    summary = watcher.run_once(
        client=drive, env=env, runner=_runner_recording(calls, code=6), now=WATCH_NOW
    )

    assert summary["failed"] == 1
    assert stt_drive.load_state(Path(env["SPEECHTOTEXT_STATE_FILE"])) == {}
    assert not Path(env["SPEECHTOTEXT_STATE_FILE"]).exists()


def test_watch_skips_a_recording_shared_with_others(tmp_path: Path) -> None:
    env = _watch_env(tmp_path)
    drive = _FakeDrive(
        [{"id": "f-shared", "name": "공유회의.m4a"}, {"id": "f-mine", "name": "내회의.m4a"}],
        shared=("f-shared",),
    )
    calls: list[tuple[list[str], dict[str, str]]] = []

    summary = watcher.run_once(
        client=drive, env=env, runner=_runner_recording(calls), now=WATCH_NOW
    )

    assert drive.downloaded == ["f-mine"]
    assert summary["skipped"] == 1
    state = json.loads(Path(env["SPEECHTOTEXT_STATE_FILE"]).read_text(encoding="utf-8"))
    assert set(state["processed"]) == {"f-mine"}


def test_watch_hands_resolved_credentials_to_the_child(tmp_path: Path) -> None:
    env = _watch_env(tmp_path)
    (tmp_path / "home" / ".env.secrets").write_text(
        "OPENAI_API_KEY=stt-secret-fixture\n", encoding="utf-8"
    )
    drive = _FakeDrive([{"id": "f-new", "name": "킥오프.m4a"}])
    calls: list[tuple[list[str], dict[str, str]]] = []

    watcher.run_once(client=drive, env=env, runner=_runner_recording(calls), now=WATCH_NOW)

    assert calls[0][1]["OPENAI_API_KEY"] == "stt-secret-fixture"


def test_watch_removes_the_downloaded_copy_after_ingest(tmp_path: Path) -> None:
    env = _watch_env(tmp_path)
    drive = _FakeDrive([{"id": "f-new", "name": "킥오프.m4a"}])
    calls: list[tuple[list[str], dict[str, str]]] = []
    watcher.run_once(client=drive, env=env, runner=_runner_recording(calls), now=WATCH_NOW)
    downloaded = Path(calls[0][0][calls[0][0].index("--file") + 1])
    assert not downloaded.exists()
    assert not downloaded.parent.exists()


def test_is_audio_matches_the_supported_suffixes() -> None:
    assert stt_drive.is_audio("회의.m4a") is True
    assert stt_drive.is_audio("회의.MP3") is True
    assert stt_drive.is_audio("메모.txt") is False


def test_folder_parts_splits_a_nested_path() -> None:
    assert stt_drive.folder_parts({"SPEECHTOTEXT_DRIVE_FOLDER": "회의/녹음"}) == ("회의", "녹음")


# --- long-form (2h+) completeness: no span of audio is silently dropped ------

import stt_coverage  # noqa: E402
import stt_media  # noqa: E402

HOUR_MS = 3_600_000


def test_merge_spans_joins_segments_separated_by_less_than_tolerance() -> None:
    merged = stt_coverage.merge_spans([(0, 5000), (5400, 9000), (60000, 61000)], tolerance_ms=1000)
    assert merged == ((0, 9000), (60000, 61000))


def test_assess_flags_a_transcription_that_stopped_early() -> None:
    # 2h recording, segments stop at 12 minutes: the classic silent truncation.
    verdict = stt_coverage.assess([(0, 720_000)], duration_ms=2 * HOUR_MS)
    assert verdict.complete is False
    assert verdict.trailing_gap_ms > HOUR_MS
    assert verdict.ratio < 0.2


def test_assess_accepts_ordinary_silence_without_alarming() -> None:
    # Long meeting with pauses: coverage is far below 1.0 yet nothing was dropped.
    spans = [(0, 1_000_000), (1_030_000, 2_000_000), (2_040_000, 7_190_000)]
    verdict = stt_coverage.assess(spans, duration_ms=2 * HOUR_MS)
    assert verdict.complete is True
    assert verdict.trailing_gap_ms < 60_000
    assert verdict.gaps == ()


def test_assess_reports_an_unexplained_internal_gap() -> None:
    spans = [(0, 600_000), (3_000_000, 7_195_000)]
    verdict = stt_coverage.assess(spans, duration_ms=2 * HOUR_MS)
    assert verdict.gaps == ((600_000, 3_000_000),)
    assert verdict.complete is False


def test_assess_without_a_known_duration_cannot_claim_completeness() -> None:
    verdict = stt_coverage.assess([(0, 600_000)], duration_ms=0)
    assert verdict.complete is False
    assert verdict.duration_ms == 0


def test_probe_duration_reads_the_ffprobe_format_field(tmp_path: Path) -> None:
    ffprobe = tmp_path / "ffprobe"
    ffprobe.write_text(
        '#!/bin/sh\nprintf \'{"format":{"duration":"7321.456000"}}\'\n', encoding="utf-8"
    )
    ffprobe.chmod(0o755)
    assert stt_media.probe_duration_ms(tmp_path / "a.m4a", ffprobe=ffprobe) == 7_321_456


def test_probe_duration_is_none_when_ffprobe_cannot_answer(tmp_path: Path) -> None:
    ffprobe = tmp_path / "ffprobe"
    ffprobe.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    ffprobe.chmod(0o755)
    assert stt_media.probe_duration_ms(tmp_path / "a.m4a", ffprobe=ffprobe) is None


def _long_form_toolchain(tmp_path: Path, monkeypatch, local_toolchain, *, last_offset_ms: int):
    """whisper stand-in emitting timestamped segments + an ffprobe reporting 2 hours."""
    binary = local_toolchain[0]
    binary.write_text(
        "#!/bin/sh\n"
        'of=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -of) of="$2"; shift 2;; *) shift;; esac; done\n'
        "printf '%s' '{\"transcription\":[{\"offsets\":{\"from\":0,\"to\":"
        + str(last_offset_ms)
        + "},\"text\":\" 회의를 시작합니다.\"}]}' > \"$of.json\"\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    ffprobe = tmp_path / "ffprobe"
    ffprobe.write_text(
        '#!/bin/sh\nprintf \'{"format":{"duration":"7200.0"}}\'\n', encoding="utf-8"
    )
    ffprobe.chmod(0o755)
    _local_env(monkeypatch, local_toolchain)
    monkeypatch.setenv("SPEECHTOTEXT_FFPROBE_BIN", str(ffprobe))


def test_local_backend_refuses_a_truncated_long_recording(
    tmp_path: Path, monkeypatch, local_toolchain
) -> None:
    _long_form_toolchain(tmp_path, monkeypatch, local_toolchain, last_offset_ms=720_000)
    audio = tmp_path / "2시간회의.m4a"
    audio.write_bytes(b"long-audio")
    with pytest.raises(stt_audio.TranscriptionRefused) as caught:
        stt_local.transcribe(
            stt_audio.check_audio(audio), stt_local.resolve_toolchain(dict(os.environ))
        )
    assert caught.value.exit_code == 8
    assert "누락" in caught.value.notice


def test_local_backend_returns_coverage_for_a_complete_long_recording(
    tmp_path: Path, monkeypatch, local_toolchain
) -> None:
    _long_form_toolchain(tmp_path, monkeypatch, local_toolchain, last_offset_ms=7_195_000)
    audio = tmp_path / "2시간회의.m4a"
    audio.write_bytes(b"long-audio")
    result = stt_local.transcribe(
        stt_audio.check_audio(audio), stt_local.resolve_toolchain(dict(os.environ))
    )
    assert result.coverage is not None
    assert result.coverage.complete is True
    assert result.coverage.duration_ms == 7_200_000


def test_local_argv_keeps_the_completeness_defaults(
    tmp_path: Path, monkeypatch, local_toolchain
) -> None:
    """-ojf for evidence, no -nf/--vad, and context carry-over OFF.

    `-mc 0` was originally left out on the documented reasoning that truncating
    context loses continuity. A real 94-minute Korean recording disproved it: with
    carry-over on, the decode fed itself its own output and 28% of the transcript
    became one sentence repeated 910 times. The same span re-decoded with `-mc 0`
    came back at the healthy 1.2% repetition of a clean sample, with the lost 28
    minutes of speech recovered.
    """
    _local_env(monkeypatch, local_toolchain)
    recorded: list[list[str]] = []
    real_run = subprocess.run

    def spy(argv, **kwargs):
        recorded.append(list(argv))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(stt_local.subprocess, "run", spy)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    stt_local.transcribe(
        stt_audio.check_audio(audio), stt_local.resolve_toolchain(dict(os.environ))
    )
    whisper_argv = recorded[1]
    assert "-ojf" in whisper_argv
    assert "-nf" not in whisper_argv
    assert "--vad" not in whisper_argv
    assert whisper_argv[whisper_argv.index("-mc") + 1] == "0"


def test_size_limit_is_the_api_cap_but_local_accepts_a_two_hour_file(tmp_path: Path) -> None:
    assert stt_audio.limit_for("api") == 25 * 1024 * 1024
    assert stt_audio.limit_for("local") > 1024 * 1024 * 1024
    big = tmp_path / "2시간회의.m4a"
    big.write_bytes(b"\0" * (26 * 1024 * 1024))
    with pytest.raises(stt_audio.TranscriptionRefused):
        stt_audio.check_audio(big, max_bytes=stt_audio.limit_for("api"))
    assert stt_audio.check_audio(big, max_bytes=stt_audio.limit_for("local")).size_bytes > 0


# --- the 25MB API path: window, transcribe, stitch without losing a second ---

import stt_chunked  # noqa: E402


def test_plan_windows_tiles_the_whole_recording_with_overlap() -> None:
    windows = stt_chunked.plan_windows(2 * HOUR_MS, window_ms=900_000, overlap_ms=10_000)
    assert windows[0] == (0, 900_000)
    assert windows[1][0] == 890_000
    assert windows[-1][1] == 2 * HOUR_MS
    assert stt_coverage.merge_spans(windows, tolerance_ms=0) == ((0, 2 * HOUR_MS),)


def test_plan_windows_returns_one_window_for_a_short_recording() -> None:
    assert stt_chunked.plan_windows(600_000, window_ms=900_000, overlap_ms=10_000) == (
        (0, 600_000),
    )


def test_stitch_drops_the_duplicated_seam() -> None:
    assert stt_chunked.stitch(
        ["오늘 회의를 시작합니다 안건은 예산입니다", "안건은 예산입니다 그리고 일정입니다"]
    ) == "오늘 회의를 시작합니다 안건은 예산입니다 그리고 일정입니다"


def test_stitch_keeps_both_sides_when_the_seam_does_not_match() -> None:
    assert stt_chunked.stitch(["첫 번째 구간입니다", "완전히 다른 내용입니다"]) == (
        "첫 번째 구간입니다 완전히 다른 내용입니다"
    )


def test_chunked_transcription_keeps_window_order_and_proves_coverage(
    tmp_path: Path, local_toolchain
) -> None:
    ffmpeg = local_toolchain[1]
    audio = tmp_path / "2시간회의.m4a"
    audio.write_bytes(b"long-audio")
    seen: list[tuple[Path, int]] = []

    def transcribe_window(chunk: Path, index: int) -> str:
        seen.append((chunk, index))
        return f"구간{index} 내용입니다"

    result = stt_chunked.transcribe_long(
        stt_audio.check_audio(audio, max_bytes=stt_audio.limit_for("local")),
        duration_ms=2 * HOUR_MS,
        ffmpeg=ffmpeg,
        transcribe_window=transcribe_window,
        model="gpt-4o-transcribe",
        window_ms=900_000,
        overlap_ms=10_000,
    )

    assert [index for _, index in seen] == list(range(len(seen)))
    assert result.text.startswith("구간0 내용입니다 구간1 내용입니다")
    assert result.coverage is not None and result.coverage.complete is True
    assert result.model == "gpt-4o-transcribe"
    assert not any(chunk.exists() for chunk, _ in seen)


def test_chunked_transcription_refuses_when_a_window_yields_nothing(
    tmp_path: Path, local_toolchain
) -> None:
    audio = tmp_path / "회의.m4a"
    audio.write_bytes(b"long-audio")
    with pytest.raises(stt_audio.TranscriptionRefused):
        stt_chunked.transcribe_long(
            stt_audio.check_audio(audio, max_bytes=stt_audio.limit_for("local")),
            duration_ms=2 * HOUR_MS,
            ffmpeg=local_toolchain[1],
            transcribe_window=lambda _chunk, _index: "   ",
            model="gpt-4o-transcribe",
        )


def test_api_path_chunks_a_recording_larger_than_the_upload_cap(
    tmp_path: Path, monkeypatch, fake_meeting, local_toolchain, capsys
) -> None:
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    monkeypatch.setenv("SPEECHTOTEXT_BACKEND", "api")
    monkeypatch.setenv("SPEECHTOTEXT_FFMPEG_BIN", str(local_toolchain[1]))
    ffprobe = tmp_path / "ffprobe"
    ffprobe.write_text(
        '#!/bin/sh\nprintf \'{"format":{"duration":"7200.0"}}\'\n', encoding="utf-8"
    )
    ffprobe.chmod(0o755)
    monkeypatch.setenv("SPEECHTOTEXT_FFPROBE_BIN", str(ffprobe))
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    calls: list[Path] = []

    def fake_transcribe(checked, **kwargs):
        calls.append(checked.path)
        return stt_client.Transcription(
            text=f"구간 {len(calls)}", model=str(kwargs.get("model", "")), endpoint="api"
        )

    monkeypatch.setattr(speechtotext_cli.stt_client, "transcribe", fake_transcribe)
    big = tmp_path / "2시간회의.m4a"
    big.write_bytes(b"\0" * (26 * 1024 * 1024))

    assert speechtotext_cli.main(["ingest", "--file", str(big), "--label", "장시간"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert len(calls) > 1
    assert summary["coverage"]["complete"] is True

def test_local_backend_passes_the_proper_noun_hint(tmp_path, monkeypatch, local_toolchain) -> None:
    """Korean proper nouns are where this model actually fails — 업무→영무, 한전기술→한정기술.

    The hint existed for the API path only, so the local backend (the default) had no
    way to be told the vocabulary of the meeting it was transcribing.
    """
    _local_env(monkeypatch, local_toolchain)
    monkeypatch.setenv("SPEECHTOTEXT_PROMPT", "고유명사: 한전기술, 포스텍, 열교환기")
    recorded: list[list[str]] = []
    real_run = subprocess.run
    monkeypatch.setattr(
        stt_local.subprocess, "run",
        lambda argv, **kw: (recorded.append(list(argv)), real_run(argv, **kw))[1],
    )
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    stt_local.transcribe(
        stt_audio.check_audio(audio), stt_local.resolve_toolchain(dict(os.environ))
    )

    whisper = recorded[1]
    assert whisper[whisper.index("--prompt") + 1] == "고유명사: 한전기술, 포스텍, 열교환기"


def test_local_backend_omits_the_hint_when_none_is_configured(
    tmp_path, monkeypatch, local_toolchain
) -> None:
    _local_env(monkeypatch, local_toolchain)
    monkeypatch.delenv("SPEECHTOTEXT_PROMPT", raising=False)
    recorded: list[list[str]] = []
    real_run = subprocess.run
    monkeypatch.setattr(
        stt_local.subprocess, "run",
        lambda argv, **kw: (recorded.append(list(argv)), real_run(argv, **kw))[1],
    )
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    stt_local.transcribe(
        stt_audio.check_audio(audio), stt_local.resolve_toolchain(dict(os.environ))
    )

    assert "--prompt" not in recorded[1]

# --- repetition collapse: timestamps stay full while the words become one phrase ---


def test_dominant_repeat_separates_a_healthy_transcript_from_a_collapsed_one() -> None:
    healthy = " ".join(f"{n}번째 안건을 논의했습니다" for n in range(200))
    assert stt_coverage.dominant_repeat(healthy)[0] < 0.10
    collapsed = healthy + " " + ("검사를 지키는 게 아니죠? " * 400)
    ratio, phrase = stt_coverage.dominant_repeat(collapsed)
    assert ratio > 0.5
    assert "검사를 지키는" in phrase


def test_local_backend_refuses_a_transcript_that_collapsed_into_one_phrase(
    tmp_path, monkeypatch, local_toolchain
) -> None:
    """A 94-minute recording lost 26 minutes to a loop while coverage read 'no gaps'.

    Segment timestamps stayed continuous, so the completeness check passed on a
    transcript that was half one repeated sentence.
    """
    binary = local_toolchain[0]
    loop = " 검사를 지키는 게 아니죠?" * 400
    binary.write_text(
        "#!/bin/sh\n"
        'of=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -of) of="$2"; shift 2;; *) shift;; esac; done\n'
        "printf '%s' '{\"transcription\":[{\"offsets\":{\"from\":0,\"to\":300000},"
        '\"text\":\"' + loop + '\"}]}\' > "$of.json"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    _local_env(monkeypatch, local_toolchain)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    with pytest.raises(stt_audio.TranscriptionRefused) as caught:
        stt_local.transcribe(
            stt_audio.check_audio(audio), stt_local.resolve_toolchain(dict(os.environ))
        )

    assert caught.value.exit_code == 8
    assert "반복" in caught.value.notice


def test_allow_incomplete_lets_a_reviewed_collapse_through(
    tmp_path, monkeypatch, local_toolchain
) -> None:
    binary = local_toolchain[0]
    loop = " 검사를 지키는 게 아니죠?" * 400
    binary.write_text(
        "#!/bin/sh\n"
        'of=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -of) of="$2"; shift 2;; *) shift;; esac; done\n'
        "printf '%s' '{\"transcription\":[{\"offsets\":{\"from\":0,\"to\":300000},"
        '\"text\":\"' + loop + '\"}]}\' > "$of.json"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    _local_env(monkeypatch, local_toolchain)
    monkeypatch.setenv("SPEECHTOTEXT_ALLOW_INCOMPLETE", "1")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    result = stt_local.transcribe(
        stt_audio.check_audio(audio), stt_local.resolve_toolchain(dict(os.environ))
    )

    assert "검사를 지키는" in result.text


# --- tidying the transcript: readable, and never shorter than what was said ----

import stt_polish  # noqa: E402

_SAID = (
    "혹시 중앙중 교수님 이 계통 열수력 평가 이걸로 정리를 해도 좋을 것 같습니다. "
    "결과물들하고 제가 지금 생각하는 내용들을 연차별로 잡아봤고요. "
    "성능 요건하고 시험 요건을 검토하는 거가 1단계입니다. "
    "병행해서 유사 제품이나 기술들을 조사하고 분석해보는 거를 두 번째로 봤고. "
    "세 번째는 요건이 나오면 그거부터 개발을 실질적으로 하는 영무를 잡아놨습니다. "
    "설계 도서들은 27년 10월까지 데드라인을 목표로 하고 있습니다. "
    "예비 제작성 검토는 28년 4월로 잡혀 있습니다. "
    "고온고압 설비 구성은 28년 2월까지 결과물로 잡혀 있습니다."
)


def test_split_sentences_keeps_every_word() -> None:
    sentences = stt_polish.split_sentences(_SAID)
    assert len(sentences) == 8
    assert " ".join(sentences) == " ".join(_SAID.split())


def test_polish_turns_one_wall_of_text_into_paragraphs(tmp_path: Path) -> None:
    """The local backend joins every segment into a single line — 38,216 characters of it."""
    wall = " ".join([_SAID] * 12)
    result = stt_polish.polish(wall)

    assert "\n\n" in result.body
    assert result.paragraphs >= 4
    assert result.sentences == 96
    # 문장 하나가 한 줄 — 1,137자짜리 줄이 나오던 문서를 대신한다.
    lines = [line for line in result.body.splitlines() if line.strip()]
    assert len(lines) == 96
    assert max(len(line) for line in lines) < 300


def test_polish_never_drops_a_distinct_sentence() -> None:
    """다듬되 버리지 않는다 — duplicated words are a nuisance, dropped words are the failure."""
    result = stt_polish.polish(_SAID)
    for sentence in stt_polish.split_sentences(_SAID):
        assert sentence in result.body


def test_polish_collapses_only_an_exact_consecutive_repeat() -> None:
    looped = "안건을 정리했습니다. " + ("검사를 지키는 게 아니죠? " * 5) + "안건을 정리했습니다."
    result = stt_polish.polish(looped)

    assert result.body.count("검사를 지키는 게 아니죠?") == 1
    assert result.collapsed == 4
    # the sentence that merely recurs later is NOT a consecutive repeat and stays twice
    assert result.body.count("안건을 정리했습니다.") == 2


def test_glossary_corrects_the_names_the_model_got_wrong(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "glossary.txt"
    path.write_text(
        "# 회의 고유명사\n\n영무=업무\n외교환기=열교환기\n", encoding="utf-8"
    )
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(path))
    glossary = stt_polish.load_glossary(dict(os.environ))

    assert glossary == (("영무", "업무"), ("외교환기", "열교환기"))
    result = stt_polish.polish(_SAID, glossary=glossary)
    assert "영무" not in result.body
    assert "업무를 잡아놨습니다" in result.body
    assert result.substitutions == 1


def test_glossary_is_empty_when_none_is_configured(tmp_path: Path, monkeypatch) -> None:
    """A guessed name written into production would harden the very mishearing it guessed."""
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(tmp_path / "absent.txt"))
    assert stt_polish.load_glossary(dict(os.environ)) == ()


def test_prompt_hint_feeds_the_same_names_to_the_model() -> None:
    hint = stt_polish.prompt_hint((("영무", "업무"), ("한정기술", "한전기술")))
    assert "업무" in hint and "한전기술" in hint
    assert stt_polish.prompt_hint(()) == ""


def test_split_document_separates_the_provenance_header(tmp_path: Path) -> None:
    document = (
        "# 킥오프 전사본\n\n- 원본 음성: a.m4a\n- 전사 모델: local:x\n\n---\n\n" + _SAID + "\n"
    )
    header, body = stt_polish.split_document(document)
    assert header.startswith("# 킥오프 전사본")
    assert body.strip() == _SAID


# --- the CLI writes the readable transcript, and Drive actually receives it ----

_WALL = " ".join([_SAID] * 12)


def test_transcribe_writes_a_readable_transcript(
    tmp_path: Path, monkeypatch, fake_meeting, capsys
) -> None:
    """One line of 38k characters is a faithful transcript and an unusable document."""
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(tmp_path / "absent.txt"))
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    spoken = tmp_path / "spoken.txt"
    spoken.write_text(_WALL, encoding="utf-8")

    assert speechtotext_cli.main(
        ["transcribe", "--file", str(audio), "--label", "킥오프", "--recorded", str(spoken)]
    ) == 0

    summary = json.loads(capsys.readouterr().out)
    document = Path(summary["transcript_path"]).read_text(encoding="utf-8")
    header, body = stt_polish.split_document(document)
    assert "- 다듬기: " in header
    assert body.count("\n\n") >= 4
    assert summary["polish"]["paragraphs"] >= 4
    assert summary["polish"]["sentences"] == 96


def test_polish_verb_retidies_an_existing_transcript(tmp_path: Path, monkeypatch, capsys) -> None:
    """The 94-minute transcript already on disk was written before tidying existed."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(tmp_path / "absent.txt"))
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    document = tmp_path / "2026-08-26_20260825_해양고신뢰성.md"
    # A real transcript carries consecutive repeats — that is the whole reason the
    # first pass collapses anything, and the case a re-run has to survive unchanged.
    document.write_text(
        "# 20260825_해양고신뢰성 전사본\n\n- 원본 음성: stt-audio.m4a\n"
        "- 전사 커버리지: 98.8% · 미검출 구간 0곳 · 누락 없음\n\n---\n\n"
        + _WALL + " " + ("검사를 지키는 게 아니죠? " * 3).strip() + "\n",
        encoding="utf-8",
    )

    assert speechtotext_cli.main(["polish", "--file", str(document)]) == 0

    summary = json.loads(capsys.readouterr().out)
    rewritten = document.read_text(encoding="utf-8")
    assert summary["label"] == "20260825_해양고신뢰성"
    assert summary["polish"]["sentences"] == 97
    assert summary["polish"]["collapsed"] == 2
    assert "- 원본 음성: stt-audio.m4a" in rewritten
    assert "- 전사 커버리지: 98.8%" in rewritten
    assert rewritten.count("- 다듬기:") == 1
    for sentence in stt_polish.split_sentences(_SAID):
        assert sentence in rewritten

    # Running it again must return the very same bytes. The file records what the
    # document IS; what a given pass DID belongs in that run's JSON, or a re-tidy of an
    # already-tidy transcript rewrites its own receipt (접음 2 → 0) and never settles.
    assert speechtotext_cli.main(["polish", "--file", str(document)]) == 0
    assert document.read_text(encoding="utf-8") == rewritten


def test_cli_loads_env_secrets_so_drive_publication_reaches_the_facade(
    tmp_path: Path, monkeypatch, fake_meeting, capsys
) -> None:
    """`DRIVE_PUBLISH_ENABLED` lives in ~/.env.secrets, and a no-agent run never has it
    in the environment — which is why the 94-minute transcript came back drive_link=""."""
    from types import SimpleNamespace

    monkeypatch.setattr(os, "environ", dict(os.environ))
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(tmp_path / "absent.txt"))
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(REPO))
    (tmp_path / "home" / ".env.secrets").write_text(
        "DRIVE_PUBLISH_ENABLED=1\nDRIVE_GWS_BIN=/opt/bin/gws\n", encoding="utf-8"
    )
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from automation import drive_outputs

    published: list[tuple[str, str]] = []

    def spy(kind, title, artifacts, **kwargs):
        published.append((kind, title))
        return SimpleNamespace(links=("https://drive.example/transcript",), action="created")

    monkeypatch.setattr(drive_outputs, "publish_best_effort", spy)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    spoken = tmp_path / "spoken.txt"
    spoken.write_text(_SAID, encoding="utf-8")

    assert speechtotext_cli.main(
        ["transcribe", "--file", str(audio), "--label", "킥오프", "--recorded", str(spoken)]
    ) == 0

    assert os.environ["DRIVE_PUBLISH_ENABLED"] == "1"
    assert os.environ["DRIVE_GWS_BIN"] == "/opt/bin/gws"
    assert published == [("transcript", "킥오프")]
    assert json.loads(capsys.readouterr().out)["drive_link"] == "https://drive.example/transcript"


def test_transcribe_hints_the_model_with_the_glossary(
    tmp_path: Path, monkeypatch, fake_meeting, local_toolchain, capsys
) -> None:
    """One file holds the names: it corrects the output AND primes the model beforehand."""
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    _local_env(monkeypatch, local_toolchain)
    monkeypatch.delenv("SPEECHTOTEXT_PROMPT", raising=False)
    glossary = tmp_path / "glossary.txt"
    glossary.write_text("영무=업무\n한정기술=한전기술\n", encoding="utf-8")
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(glossary))
    recorded: list[list[str]] = []
    real_run = subprocess.run
    monkeypatch.setattr(
        stt_local.subprocess, "run",
        lambda argv, **kw: (recorded.append(list(argv)), real_run(argv, **kw))[1],
    )
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    assert speechtotext_cli.main(["transcribe", "--file", str(audio), "--label", "킥오프"]) == 0

    whisper = recorded[1]
    assert whisper[whisper.index("--prompt") + 1] == "고유명사: 업무, 한전기술"


# --- one glossary per research project, and outputs filed beside it ------------

import stt_runtime  # noqa: E402


def test_project_is_the_first_token_that_is_not_a_date() -> None:
    """The owner names the recording; the name says which project it belongs to."""
    assert stt_runtime.project_of("20260825_해양고신뢰성") == "해양고신뢰성"
    assert stt_runtime.project_of("2026-08-25_해양고신뢰성_킥오프") == "해양고신뢰성"
    assert stt_runtime.project_of("해양고신뢰성_킥오프") == "해양고신뢰성"
    assert stt_runtime.project_of("해양고신뢰성") == "해양고신뢰성"
    assert stt_runtime.project_of("20260825") == ""
    assert stt_runtime.project_of("") == ""


def test_parse_glossary_reads_the_text_the_file_holds() -> None:
    """주석과 빈 줄은 항목이 아니고, 한 칸짜리 줄은 **바른 용어**다.

    2026-09-05 전에는 구분자 없는 줄을 버렸다. 그때는 용어집이 1:1 쌍뿐이라 그것이 잘못 쓴
    줄이었지만, 지금은 소유자가 틀리는 방식을 모른 채 바른 용어만 적는 정상 형식이다.
    """
    parsed = stt_polish.parse_glossary("# 주석\n영무=업무\n\n한정기술=한전기술\n열교환기\n")

    assert parsed == (("영무", "업무"), ("한정기술", "한전기술"), ("열교환기", "열교환기"))


class _FakeProjectDrive:
    """Enough Drive to hold one project folder with one glossary file in it."""

    def __init__(self, text: str | None) -> None:
        self.text = text
        self.resolved: list[tuple[str, ...]] = []

    def ensure_folder_path(self, parts):
        raise AssertionError(f"용어집 조회가 폴더를 만들었다: {tuple(parts)}")

    def find_folder_path(self, parts):
        self.resolved.append(tuple(parts))
        return "project-folder"

    def list_children(self, folder_id):
        assert folder_id == "project-folder"
        return [] if self.text is None else [{"id": "g1", "name": "용어집.txt"}]

    def download_file(self, file_id: str, dest: Path) -> str:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.text or "", encoding="utf-8")
        return "sha"


class _FakeNestedDrive:
    """Drive with a 용어집.txt at some folders on the path — and no folder creation."""

    def __init__(self, glossaries, *, folders=None, legacy=None, fails: bool = False) -> None:
        self.glossaries = {tuple(key): value for key, value in glossaries.items()}
        self.legacy = {tuple(key): value for key, value in (legacy or {}).items()}
        known = set(self.glossaries) | set(self.legacy)
        self.folders = {tuple(f) for f in (known if folders is None else folders)}
        self.fails = fails
        self.looked: list[tuple[str, ...]] = []

    def ensure_folder_path(self, parts):
        raise AssertionError(f"용어집 조회가 폴더를 만들었다: {tuple(parts)}")

    def find_folder_path(self, parts):
        if self.fails:
            raise RuntimeError("drive is unreachable")
        key = tuple(parts)
        self.looked.append(key)
        return "|".join(key) if key in self.folders else None

    def list_children(self, folder_id):
        key = tuple(folder_id.split("|"))
        kids = []
        if key in self.glossaries:
            kids.append({"id": f"{folder_id}|csv", "name": "용어집.csv"})
        if key in self.legacy:
            kids.append({"id": f"{folder_id}|txt", "name": "용어집.txt"})
        return kids

    def download_file(self, file_id: str, dest: Path) -> str:
        *parts, kind = file_id.split("|")
        source = self.glossaries if kind == "csv" else self.legacy
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source[tuple(parts)], encoding="utf-8")
        return "sha"


def test_the_glossary_is_nested_and_the_deeper_folder_wins(tmp_path, monkeypatch) -> None:
    """트리가 중첩이니 용어집도 중첩이다 — 루트에 한 번 적은 이름이 모든 녹음에 걸린다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPEECHTOTEXT_GLOSSARY", raising=False)
    drive = _FakeNestedDrive(
        {
            ("autophagy",): "영무=업무\n한정기술=바깥값\n",
            ("autophagy", "전사본"): "한정기술=전사본값\n",
            ("autophagy", "전사본", "해양고신뢰성"): "한정기술=한전기술\n",
        }
    )

    merged = dict(stt_runtime.merged_glossary("해양고신뢰성", client=drive))

    assert merged["영무"] == "업무"
    assert merged["한정기술"] == "한전기술"


def test_the_outer_layers_are_looked_up_from_the_root_down(tmp_path, monkeypatch) -> None:
    """조회는 바깥에서 안으로 — 그리고 조회가 폴더를 만들면 fake 가 실패시킨다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPEECHTOTEXT_GLOSSARY", raising=False)
    drive = _FakeNestedDrive({("autophagy", "전사본"): "영무=업무\n"})

    assert stt_runtime.glossary(client=drive) == (("영무", "업무"),)
    assert drive.looked == [("autophagy",), ("autophagy", "전사본")]


def test_a_missing_outer_folder_does_not_hide_the_inner_glossary(tmp_path, monkeypatch) -> None:
    """바깥 층이 없는 것은 정상이다 — 안쪽 층이 그 때문에 사라지면 안 된다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPEECHTOTEXT_GLOSSARY", raising=False)
    drive = _FakeNestedDrive(
        {("autophagy", "전사본", "해양고신뢰성"): "한정기술=한전기술\n"},
        folders={("autophagy", "전사본", "해양고신뢰성")},
    )

    assert dict(stt_runtime.merged_glossary("해양고신뢰성", client=drive)) == {"한정기술": "한전기술"}


def test_a_fetched_glossary_is_cached_on_the_node(tmp_path, monkeypatch) -> None:
    """캐시가 있어야 Drive 옵트아웃 경로(plaud lifelog)도 같은 이름을 고친다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPEECHTOTEXT_GLOSSARY", raising=False)

    stt_runtime.glossary(client=_FakeNestedDrive({("autophagy", "전사본"): "영무=업무\n"}))

    cached = tmp_path / ".hermes/speechtotext/glossary.txt"
    assert cached.read_text(encoding="utf-8") == "영무=업무\n"
    assert stt_polish.load_glossary({}) == (("영무", "업무"),)


def test_a_correct_term_is_cached_as_one_column(tmp_path, monkeypatch) -> None:
    """캐시는 정본의 거울이다 — 한 칸으로 받은 것을 두 칸으로 적으면 형식을 오해하게 된다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPEECHTOTEXT_GLOSSARY", raising=False)

    stt_runtime.glossary(client=_FakeNestedDrive({("autophagy", "전사본"): "열교환기\n영무,업무\n"}))

    cached = tmp_path / ".hermes/speechtotext/glossary.txt"
    assert cached.read_text(encoding="utf-8") == "열교환기\n영무=업무\n"


def test_the_node_cache_answers_when_drive_will_not(tmp_path, monkeypatch, capsys) -> None:
    """Drive 불통이 용어집을 비우면 안 된다 — 그 침묵이 오인식을 굳힌다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPEECHTOTEXT_GLOSSARY", raising=False)
    cached = tmp_path / ".hermes/speechtotext/glossary.txt"
    cached.parent.mkdir(parents=True)
    cached.write_text("영무=업무\n", encoding="utf-8")

    pairs = stt_runtime.glossary(client=_FakeNestedDrive({}, fails=True))

    assert pairs == (("영무", "업무"),)
    assert "GLOSSARY-FETCH-FAIL" in capsys.readouterr().err


def test_a_glossary_absent_from_drive_is_absent_here_too(tmp_path, monkeypatch, capsys) -> None:
    """Drive 가 정본이므로 거기 없으면 없는 것이다 — 낡은 캐시를 정본으로 승격하지 않는다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPEECHTOTEXT_GLOSSARY", raising=False)
    cached = tmp_path / ".hermes/speechtotext/glossary.txt"
    cached.parent.mkdir(parents=True)
    cached.write_text("영무=업무\n", encoding="utf-8")

    assert stt_runtime.glossary(client=_FakeNestedDrive({}, folders={("autophagy",)})) == ()
    assert cached.read_text(encoding="utf-8") == ""
    assert "GLOSSARY-DRIVE-ABSENT" in capsys.readouterr().err


def test_an_explicit_glossary_path_is_read_without_touching_drive(tmp_path, monkeypatch) -> None:
    """명시한 파일은 명시한 대로 — 샌드박스·오프라인의 결정성이 여기 걸려 있다."""
    local = tmp_path / "glossary.txt"
    local.write_text("영무=업무\n", encoding="utf-8")
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(local))
    drive = _FakeNestedDrive({("autophagy", "전사본"): "영무=틀린값\n"})

    assert stt_runtime.glossary(client=drive) == (("영무", "업무"),)
    assert drive.looked == []


def test_a_glossary_row_may_be_a_table_row_or_an_equals_line() -> None:
    """표(csv)로 적든 예전처럼 =로 적든 같은 용어집이다 — 머리글과 주석은 읽지 않는다."""
    parsed = stt_polish.parse_glossary(
        "틀린표기,올바른표기\n영무,업무\n한정기술=한전기술\n# 작성 예시\n# 포스텔,포스텍\n"
    )

    assert parsed == (("영무", "업무"), ("한정기술", "한전기술"))


def test_a_quoted_table_field_keeps_its_comma() -> None:
    """Sheets 는 쉼표가 든 칸을 따옴표로 감싼다 — 손으로 자르면 그 줄이 쓰레기가 된다."""
    parsed = stt_polish.parse_glossary('"서울, 부산","서울·부산"\n영무,업무\n')

    assert parsed == (("서울, 부산", "서울·부산"), ("영무", "업무"))


def test_the_table_glossary_wins_over_a_legacy_text_file(tmp_path, monkeypatch) -> None:
    """한 폴더에 둘 다 있으면 새 이름이 정본이다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPEECHTOTEXT_GLOSSARY", raising=False)
    drive = _FakeNestedDrive(
        {("autophagy", "전사본"): "영무,업무\n"},
        legacy={("autophagy", "전사본"): "영무=옛값\n"},
    )

    assert stt_runtime.glossary(client=drive) == (("영무", "업무"),)


def test_a_legacy_text_glossary_is_still_read(tmp_path, monkeypatch) -> None:
    """이미 Drive 에 있는 용어집.txt 가 이름이 바뀌었다고 안 읽히면 조용한 회귀다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPEECHTOTEXT_GLOSSARY", raising=False)
    drive = _FakeNestedDrive({}, legacy={("autophagy", "전사본"): "영무=업무\n"})

    assert stt_runtime.glossary(client=drive) == (("영무", "업무"),)


def test_the_shipped_example_documents_the_format_without_adding_entries() -> None:
    """예시 파일을 그대로 올려도 항목이 생기지 않는다 — 전부 주석이어야 한다."""
    example = (
        Path(__file__).resolve().parents[2]
        / "skills/speechtotext/configs/용어집.example.csv"
    )

    assert stt_polish.parse_glossary(example.read_text(encoding="utf-8")) == ()


def test_project_glossary_is_read_from_the_project_folder(monkeypatch) -> None:
    drive = _FakeProjectDrive("한정기술=한전기술\n포스텔=포스텍\n")

    pairs = stt_runtime.project_glossary("해양고신뢰성", client=drive)

    assert drive.resolved == [("autophagy", "전사본", "해양고신뢰성")]
    assert pairs == (("한정기술", "한전기술"), ("포스텔", "포스텍"))


def test_project_glossary_is_empty_when_the_project_has_none() -> None:
    assert stt_runtime.project_glossary("해양고신뢰성", client=_FakeProjectDrive(None)) == ()
    assert stt_runtime.project_glossary("", client=_FakeProjectDrive("영무=업무\n")) == ()


def test_project_entries_win_over_the_global_glossary(tmp_path, monkeypatch) -> None:
    """Generic mishearings belong to every project; institution names belong to one."""
    glob = tmp_path / "glossary.txt"
    glob.write_text("영무=업무\n한정기술=틀린값\n", encoding="utf-8")
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(glob))

    merged = stt_runtime.merged_glossary(
        "해양고신뢰성", client=_FakeProjectDrive("한정기술=한전기술\n")
    )

    assert dict(merged)["영무"] == "업무"
    assert dict(merged)["한정기술"] == "한전기술"


def test_transcribe_files_the_transcript_under_its_project(
    tmp_path: Path, monkeypatch, fake_meeting, capsys
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(os, "environ", dict(os.environ))
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(tmp_path / "absent.txt"))
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(REPO))
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from automation import drive_outputs

    published: list[tuple[str, str, object]] = []
    monkeypatch.setattr(
        drive_outputs, "publish_best_effort",
        lambda kind, title, artifacts, **kw: (
            published.append((kind, title, kw.get("project"))),
            SimpleNamespace(links=("https://drive.example/t",)),
        )[1],
    )
    monkeypatch.setattr(stt_runtime, "project_glossary", lambda project, **kw: ())
    audio = tmp_path / "20260825_해양고신뢰성.wav"
    audio.write_bytes(b"RIFF")
    spoken = tmp_path / "spoken.txt"
    spoken.write_text(_SAID, encoding="utf-8")

    assert speechtotext_cli.main(
        ["transcribe", "--file", str(audio), "--recorded", str(spoken)]
    ) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["project"] == "해양고신뢰성"
    assert published == [("transcript", "20260825_해양고신뢰성", "해양고신뢰성")]


def test_ingest_hands_the_project_to_the_meeting_child(
    tmp_path: Path, monkeypatch, fake_meeting, capsys
) -> None:
    _cli_env(tmp_path, monkeypatch, fake_meeting)
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(tmp_path / "absent.txt"))
    monkeypatch.setattr(stt_runtime, "project_glossary", lambda project, **kw: ())
    audio = tmp_path / "20260825_해양고신뢰성.wav"
    audio.write_bytes(b"RIFF")
    spoken = tmp_path / "spoken.txt"
    spoken.write_text(_SAID, encoding="utf-8")

    assert speechtotext_cli.main(
        ["ingest", "--file", str(audio), "--recorded", str(spoken)]
    ) == 0

    argv = json.loads(fake_meeting[1].read_text(encoding="utf-8"))["argv"]
    assert argv[argv.index("--project") + 1] == "해양고신뢰성"


def test_project_glossary_never_builds_a_client_without_the_opt_in(monkeypatch) -> None:
    """Reading a project glossary is a Drive call, and a Drive call is opt-in.

    Without this the CLI reached the owner's real Drive from a unit test — the run that
    found it created a folder named after a test fixture.
    """
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from automation import drive_outputs

    def explode() -> None:
        raise AssertionError("a Drive client must not be built without DRIVE_PUBLISH_ENABLED=1")

    monkeypatch.setattr(drive_outputs, "client_from_environment", explode)

    assert stt_runtime.project_glossary("해양고신뢰성") == ()
    assert stt_runtime.merged_glossary("해양고신뢰성") == stt_runtime.glossary()


# --- 창 단위 전사: 한 구간의 사고가 나머지 전사본을 데려가지 않는다 -------------
#
# 2026-09-04(t_4e3d6630): 2시간 녹음이 두 번 실패하고 요약도 전사본도 없는 노트가
# 발행됐다. 원인은 두 겹이었다 — (1) whisper.cpp 가 쓴 JSON 의 깨진 UTF-8 을
# `read_text()` 가 UnicodeDecodeError 로 터뜨렸고 그 예외는 `json.JSONDecodeError`
# 핸들러를 그대로 빠져나갔다, (2) 2시간을 한 번의 프로세스·한 번의 읽기로 처리해
# 바이트 하나가 두 시간을 전부 버렸다. 아래 테스트는 그 두 겹을 각각 고정한다.

_FAKE_FFMPEG_WAV = '''#!/usr/bin/env python3
"""ffmpeg stand-in: a real 16 kHz mono wav whose length the window planner can read."""
import os
import sys
import wave

seconds = float(os.environ.get("FAKE_WAV_SECONDS", "330"))
with wave.open(sys.argv[-1], "wb") as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(16000)
    handle.writeframes(b"\\0" * int(16000 * 2 * seconds))
'''

_FAKE_WHISPER_WINDOWS = '''#!/usr/bin/env python3
"""whisper-cli stand-in: one payload per window, as described by a plan file."""
import json
import os
import sys

argv = sys.argv[1:]


def flag(name, fallback=""):
    return argv[argv.index(name) + 1] if name in argv else fallback


offset = int(flag("-ot", "0"))
duration = int(flag("-d", "0"))
out = flag("-of")
with open(os.environ["FAKE_WHISPER_PLAN"], encoding="utf-8") as handle:
    plan = json.load(handle)
with open(os.environ["FAKE_WHISPER_LOG"], "a", encoding="utf-8") as handle:
    handle.write("%d,%d\\n" % (offset, duration))
behaviour = plan.get(str(offset), plan.get("default", "ok"))
if behaviour == "rc":
    sys.stderr.write("fake whisper refused window %d\\n" % offset)
    raise SystemExit(3)
end = offset + duration if duration else int(float(os.environ["FAKE_WAV_SECONDS"]) * 1000)
text = " 구간 %d 발화입니다." % offset
if behaviour == "bad-utf8":
    text = " 구간 %d @@발화입니다." % offset
if behaviour == "repeat":
    text = " 같은 말을 반복합니다." * 400
raw = json.dumps(
    {"transcription": [{"offsets": {"from": offset, "to": end}, "text": text}]},
    ensure_ascii=False,
).encode("utf-8")
if behaviour == "bad-utf8":
    raw = raw.replace(b"@@", b"\\xed\\xa0")
if behaviour == "invalid-json":
    raw = raw[: len(raw) // 2]
with open(out + ".json", "wb") as handle:
    handle.write(raw)
'''


def _windowed_env(
    tmp_path: Path,
    monkeypatch,
    behaviour: dict[str, str],
    *,
    seconds: float = 330.0,
    probe_seconds: float | None = None,
) -> tuple[Path, Path, Path]:
    """A 5.5-minute recording, 2-minute windows: three windows over one wav."""
    binary = tmp_path / "whisper-cli"
    binary.write_text(_FAKE_WHISPER_WINDOWS, encoding="utf-8")
    binary.chmod(0o755)
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text(_FAKE_FFMPEG_WAV, encoding="utf-8")
    ffmpeg.chmod(0o755)
    ffprobe = tmp_path / "ffprobe"
    ffprobe.write_text(
        f'#!/bin/sh\nprintf \'{{"format":{{"duration":"{probe_seconds or seconds}"}}}}\'\n',
        encoding="utf-8",
    )
    ffprobe.chmod(0o755)
    model = tmp_path / "ggml-large-v3-turbo-q5_0.bin"
    model.write_bytes(b"ggml-model-fixture")
    plan = tmp_path / "whisper-plan.json"
    plan.write_text(json.dumps(behaviour), encoding="utf-8")
    log = tmp_path / "whisper-calls.log"
    cache = tmp_path / "window-cache"
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_BIN", str(binary))
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_MODEL", str(model))
    monkeypatch.setenv("SPEECHTOTEXT_FFMPEG_BIN", str(ffmpeg))
    monkeypatch.setenv("SPEECHTOTEXT_FFPROBE_BIN", str(ffprobe))
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_MS", "120000")
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_OVERLAP_MS", "15000")
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_CACHE", str(cache))
    monkeypatch.setenv("FAKE_WHISPER_PLAN", str(plan))
    monkeypatch.setenv("FAKE_WHISPER_LOG", str(log))
    monkeypatch.setenv("FAKE_WAV_SECONDS", str(seconds))
    monkeypatch.delenv("SPEECHTOTEXT_ALLOW_INCOMPLETE", raising=False)
    return plan, log, cache


def _recording(tmp_path: Path) -> stt_audio.CheckedAudio:
    audio = tmp_path / "2시간회의.m4a"
    audio.write_bytes(b"long-audio-fixture")
    return stt_audio.check_audio(audio)


def _windowed_transcribe(tmp_path: Path):
    return stt_local.transcribe(
        _recording(tmp_path), stt_local.resolve_toolchain(dict(os.environ))
    )


def _calls(log: Path) -> list[tuple[int, int]]:
    if not log.exists():
        return []
    return [
        (int(line.split(",")[0]), int(line.split(",")[1]))
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _cache_entries(cache: Path) -> list[str]:
    return [path.name for path in cache.iterdir() if path.name not in {"quarantine", "partial"}]


def test_each_window_runs_its_own_whisper_process_over_the_same_wav(
    tmp_path: Path, monkeypatch
) -> None:
    """한 번의 프로세스로 두 시간을 걸지 않는다 — 창마다 -ot/-d 로 따로 건다."""
    _plan, log, _cache = _windowed_env(tmp_path, monkeypatch, {})

    result = _windowed_transcribe(tmp_path)

    assert _calls(log) == [(0, 120_000), (105_000, 120_000), (210_000, 120_000)]
    assert result.text == (
        "구간 0 발화입니다. 구간 105000 발화입니다. 구간 210000 발화입니다."
    )
    assert result.coverage is not None and result.coverage.complete is True


def test_a_window_with_broken_utf8_keeps_its_words_instead_of_killing_the_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """사고 재현: JSON 한복판의 깨진 UTF-8 — 깨진 자리만 대체되고 전사는 이어진다."""
    _windowed_env(tmp_path, monkeypatch, {"105000": "bad-utf8"})

    result = _windowed_transcribe(tmp_path)

    assert "구간 0 발화입니다." in result.text
    assert "구간 210000 발화입니다." in result.text
    assert "구간 105000" in result.text
    assert "\ufffd" in result.text
    assert "WHISPER-WINDOW-REPAIRED index=1" in capsys.readouterr().err


def test_a_window_that_fails_is_quarantined_and_the_others_still_reach_the_transcript(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """사고 재현: 한 창의 실패가 두 시간을 버리던 자리 — 이제 그 창만 비어 있다."""
    _windowed_env(tmp_path, monkeypatch, {"105000": "rc"})

    result = _windowed_transcribe(tmp_path)

    assert "구간 0 발화입니다." in result.text
    assert "구간 210000 발화입니다." in result.text
    # 창 1 은 00:01:45–00:03:45 지만 마지막 15초는 창 2 와 겹치고 그 겹침은 창 2 가
    # 전사했다. 표지가 부르는 것은 **아무 창도 전사하지 못한 분**뿐이다.
    assert "[전사 실패 구간 00:01:45–00:03:30" in result.text
    stderr = capsys.readouterr().err
    assert "WHISPER-WINDOW-QUARANTINED index=1 reason=rc=3" in stderr
    assert result.coverage is not None and result.coverage.complete is True


def test_an_unparseable_window_payload_is_copied_out_for_forensics(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _plan, _log, cache = _windowed_env(tmp_path, monkeypatch, {"210000": "invalid-json"})

    result = _windowed_transcribe(tmp_path)

    copies = sorted((cache / "quarantine").rglob("*.json"))
    assert len(copies) == 1
    assert copies[0].read_bytes().startswith(b'{"transcription"')
    assert "[전사 실패 구간 00:03:30–00:05:30" in result.text
    assert "구간 0 발화입니다." in result.text
    assert "reason=invalid-json" in capsys.readouterr().err


def test_a_window_that_collapsed_into_one_phrase_is_dropped_alone(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """반복 붕괴는 그 창의 사고다 — 나머지 창의 말까지 버릴 이유가 없다."""
    _windowed_env(tmp_path, monkeypatch, {"105000": "repeat"})

    result = _windowed_transcribe(tmp_path)

    assert "구간 0 발화입니다." in result.text
    assert "구간 210000 발화입니다." in result.text
    assert "같은 말을 반복합니다" not in result.text
    assert "WHISPER-WINDOW-QUARANTINED index=1 reason=repetition" in capsys.readouterr().err


def test_a_rerun_reuses_the_cached_windows_and_only_retries_the_failed_one(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """45분짜리 로컬 전사가 살아남는 이유 — 성공한 창은 다시 돌리지 않는다."""
    plan, log, cache = _windowed_env(tmp_path, monkeypatch, {"105000": "rc"})

    first = _windowed_transcribe(tmp_path)

    assert "[전사 실패 구간" in first.text
    assert [call[0] for call in _calls(log)] == [0, 105_000, 210_000]
    assert _cache_entries(cache)  # 완전 성공이 아니었으니 캐시는 남는다
    log.unlink()
    # 재실행에서 다시 불리면 실패하도록 바꿔 둔다: 캐시를 쓰지 않으면 바로 드러난다.
    plan.write_text(json.dumps({"0": "rc", "210000": "rc"}), encoding="utf-8")

    second = stt_local.transcribe(
        stt_audio.check_audio(tmp_path / "2시간회의.m4a"),
        stt_local.resolve_toolchain(dict(os.environ)),
    )

    assert [call[0] for call in _calls(log)] == [105_000]
    assert "[전사 실패 구간" not in second.text
    assert "구간 0 발화입니다." in second.text
    assert "구간 105000 발화입니다." in second.text
    assert "WHISPER-WINDOW-CACHED index=0" in capsys.readouterr().err
    assert _cache_entries(cache) == []  # 완전 성공 뒤에는 지운다


def test_a_refusal_keeps_and_names_the_partial_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    """거부하더라도 이미 전사한 말은 파일로 남기고 그 경로를 알린다."""
    _plan, _log, cache = _windowed_env(tmp_path, monkeypatch, {}, probe_seconds=7200.0)

    with pytest.raises(stt_audio.TranscriptionRefused) as caught:
        _windowed_transcribe(tmp_path)

    assert caught.value.exit_code == 8
    kept = sorted((cache / "partial").glob("*.md"))
    assert len(kept) == 1
    assert str(kept[0]) in caught.value.notice
    assert "구간 0 발화입니다." in kept[0].read_text(encoding="utf-8")


def test_every_window_failing_refuses_instead_of_publishing_only_markers(
    tmp_path: Path, monkeypatch
) -> None:
    _windowed_env(tmp_path, monkeypatch, {"default": "rc"})

    with pytest.raises(stt_audio.TranscriptionRefused) as caught:
        _windowed_transcribe(tmp_path)

    assert caught.value.exit_code == 8
    assert "전사" in caught.value.notice
