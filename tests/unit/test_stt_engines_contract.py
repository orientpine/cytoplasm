"""격리 엔진의 CLI 경계는 실제 모델·네트워크·GPU 없이 검증한다."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import wave

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "configs/stt-engines"
CLI = SERVICE / "stt_engines_cli.py"


@pytest.fixture
def modules(monkeypatch):
    assert CLI.is_file(), "격리 CLI 소스가 있어야 한다"
    monkeypatch.syspath_prepend(str(SERVICE))
    return importlib.import_module("stt_engines_cli"), importlib.import_module("stt_engines_runtime")


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "$(touch INJECTED); audio.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 16000)
    return path


def test_deliverable_exists():
    assert CLI.is_file(), "격리 CLI 소스가 있어야 한다"


@pytest.mark.parametrize("command", ["diarize", "align", "prepare"])
def test_missing_token_real_entry_has_zero_network(command, wav, tmp_path):
    assert CLI.is_file(), "격리 CLI 소스가 있어야 한다"
    args = [command]
    if command != "prepare":
        args += ["--wav", str(wav)]
    if command == "align":
        segments = tmp_path / "segments.json"
        segments.write_text('[{"text":"가","start":0,"end":1}]', encoding="utf-8")
        args += ["--segments", str(segments)]
    harness = """
import runpy, sys
calls = []
def audit(event, args):
    if event.startswith('socket.') or event == 'subprocess.Popen':
        calls.append(event)
        raise AssertionError('external effect before token gate')
sys.addaudithook(audit)
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except SystemExit as result:
    print('network_calls=' + str(len(calls)))
    raise SystemExit(result.code)
"""
    env = {key: value for key, value in os.environ.items() if key != "HF_TOKEN"}
    result = subprocess.run(
        [sys.executable, "-c", harness, str(CLI), *args],
        env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 4
    assert result.stdout == "network_calls=0\n"
    assert result.stderr == "STT-ENGINES-NO-TOKEN\n"


@pytest.mark.parametrize("extra", [
    ["--wav", ""], ["--num-speakers", "0"], ["--num-speakers", "wrong"],
    ["--num-speakers", "2", "--min", "1"], ["--min", "3", "--max", "2"],
    ["--min", "1"], ["--mode", "bogus"],
])
def test_malformed_arguments_before_worker(modules, monkeypatch, wav, extra):
    cli, _ = modules
    monkeypatch.setenv("HF_TOKEN", "test-only-placeholder")
    with pytest.raises(SystemExit) as failure:
        cli.parse_args(["diarize", "--wav", str(wav), *extra])
    assert failure.value.code == 2


def test_segments_validate_before_model(modules, wav, tmp_path):
    cli, _ = modules
    path = tmp_path / "segments.json"
    for content in ['{}', '[{"text":"가","start":-1,"end":1}]',
                    '[{"text":"가","start":0,"end":NaN}]',
                    '[{"text":"가","start":0,"end":2}]']:
        path.write_text(content, encoding="utf-8")
        with pytest.raises(SystemExit) as failure:
            cli.parse_args(["align", "--wav", str(wav), "--segments", str(path)])
        assert failure.value.code == 2


@pytest.fixture
def backend(modules, monkeypatch, tmp_path):
    _, runtime = modules
    monkeypatch.setenv("HF_TOKEN", "test-only-placeholder")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    calls = []
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False), device=lambda name: name,
        from_numpy=lambda data: SimpleNamespace(unsqueeze=lambda axis: data),
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(
        frombuffer=lambda data, dtype: SimpleNamespace(astype=lambda kind: FakeSamples()),
    ))

    class Annotation:
        def __init__(self, rows):
            self.rows = rows

        def itertracks(self, yield_label):
            assert yield_label is True
            return iter((SimpleNamespace(start=a, end=b), None, c) for a, b, c in self.rows)

    class Pipeline:
        @classmethod
        def from_pretrained(cls, model, **kwargs):
            calls.append((model, kwargs, os.environ["HF_HUB_OFFLINE"]))
            print("third-party noise must not enter protocol")
            return cls()

        def to(self, device):
            assert device == "cpu"
            return self

        def __call__(self, audio, **kwargs):
            calls.append((audio, kwargs))
            return SimpleNamespace(
                exclusive_speaker_diarization=Annotation([(0, .4, "$(touch BAD)"), (.4, 1, "B")]),
                speaker_diarization=Annotation([(0, .8, "$(touch BAD)"), (.4, 1, "B")]),
            )

    monkeypatch.setitem(sys.modules, "pyannote.audio", SimpleNamespace(Pipeline=Pipeline))
    monkeypatch.setitem(sys.modules, "huggingface_hub.file_download", SimpleNamespace(
        http_backoff=lambda *args, **kwargs: None,
    ))
    return runtime, calls, tmp_path


class FakeSamples:
    def __truediv__(self, divisor):
        assert divisor == 32768.0
        return self


@pytest.mark.parametrize("mode,end", [("exclusive", 400), ("regular", 800)])
def test_real_adapter_output_parses_and_cpu_is_visible(backend, modules, monkeypatch, wav, capsys, mode, end):
    runtime, calls, _ = backend
    cli, _ = modules
    monkeypatch.syspath_prepend(str(ROOT / "skills/speechtotext/scripts"))
    parser = importlib.import_module("stt_diarize")
    args = cli.parse_args(["diarize", "--wav", str(wav), "--mode", mode, "--num-speakers", "2"])
    assert runtime.run(args) == 0
    output = capsys.readouterr()
    turns = parser.parse_output(output.out)
    assert [(t.start_ms, t.end_ms, t.speaker) for t in turns] == [(0, end, 0), (400, 1000, 1)]
    assert output.err == "STT-ENGINES-CPU-ONLY\n"
    assert calls[1][1] == {"num_speakers": 2}
    assert not (wav.parent / "BAD").exists()


def test_cache_first_success_then_offline(backend, modules, wav, capsys):
    runtime, calls, tmp_path = backend
    cli, _ = modules
    args = cli.parse_args(["diarize", "--wav", str(wav)])
    assert runtime.run(args) == 0
    assert runtime.run(args) == 0
    assert [call[2] for call in calls if len(call) == 3] == ["0", "1"]
    assert list((tmp_path / "hf").rglob("ready"))
    assert not list((tmp_path / "hf").rglob("pending"))
    capsys.readouterr()


@pytest.mark.parametrize("failure", [RuntimeError("private detail"), KeyboardInterrupt(), SystemExit(143)])
def test_partial_cache_not_promoted_and_can_resume(backend, modules, monkeypatch, wav, capsys, failure):
    runtime, _, tmp_path = backend
    cli, _ = modules
    args = cli.parse_args(["diarize", "--wav", str(wav)])
    original = runtime.diarize

    def fail(*args):
        raise failure

    monkeypatch.setattr(runtime, "diarize", fail)
    with pytest.raises(type(failure)):
        runtime.run(args)
    assert not list((tmp_path / "hf").rglob("ready"))
    assert not list((tmp_path / "hf").rglob("pending"))
    monkeypatch.setattr(runtime, "diarize", original)
    assert runtime.run(args) == 0
    capsys.readouterr()


def test_gate_url_uses_submodel_not_token(modules):
    _, runtime = modules
    class GatedError(RuntimeError):
        response = SimpleNamespace(
            status_code=403,
            url="https://huggingface.co/pyannote/segmentation-3.0/resolve/main/model.bin?token=private",
            headers={"X-Error-Code": "GatedRepo"},
        )

    cause = GatedError("never log this")
    wrapped = ValueError("loader wrapper")
    wrapped.__context__ = cause
    assert runtime.gated_url(wrapped) == "https://huggingface.co/pyannote/segmentation-3.0"
    assert runtime.gated_url(RuntimeError("https://evil.invalid/private")) is None


def test_align_ko_characters_and_oov_null(backend, modules, monkeypatch, wav, tmp_path, capsys):
    runtime, _, _ = backend
    cli, _ = modules
    calls = []

    def load_align_model(**kwargs):
        calls.append(kwargs)
        return object(), {"dictionary": {"가": 1}, "language": "ko"}

    def align(segments, model, metadata, audio, device, **kwargs):
        assert kwargs == {"return_char_alignments": True, "interpolate_method": "ignore"}
        return {"segments": [{"chars": [
            {"char": "가", "start": .1, "end": .3, "score": .9},
            {"char": "7", "start": .3, "end": .4, "score": .8},
            {"char": "!", "start": -1, "end": -1, "score": -1},
        ]}]}

    monkeypatch.setitem(sys.modules, "whisperx.alignment", SimpleNamespace(
        load_align_model=load_align_model, align=align,
    ))
    monkeypatch.setitem(sys.modules, "nltk", SimpleNamespace(data=SimpleNamespace(find=lambda name: True)))
    path = tmp_path / "segments.json"
    path.write_text('[{"text":"가7!","start":0,"end":1}]', encoding="utf-8")
    assert runtime.run(cli.parse_args(["align", "--wav", str(wav), "--segments", str(path)])) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == [
        {"text": "가", "start_ms": 100, "end_ms": 300, "score": .9},
        {"text": "7", "start_ms": None, "end_ms": None, "score": None},
        {"text": "!", "start_ms": None, "end_ms": None, "score": None},
    ]
    assert calls[0]["language_code"] == "ko"
    assert calls[0]["model_name"] == "kresnik/wav2vec2-large-xlsr-korean"


def test_worker_timeout_kills_group_without_retry(modules, monkeypatch, capsys):
    cli, _ = modules
    calls = []

    class Process:
        pid = 123
        returncode = -9

        def communicate(self, timeout=None):
            calls.append(timeout)
            if timeout is not None:
                raise subprocess.TimeoutExpired(["worker"], timeout)
            return "", ""

    def start(argv, **kwargs):
        assert isinstance(argv, list)
        assert kwargs["start_new_session"] is True
        assert not kwargs.get("shell", False)
        return Process()

    monkeypatch.setattr(subprocess, "Popen", start)
    killed = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    assert cli.run_worker(["prepare"], 9) == 124
    assert calls == [9, None]
    assert killed == [(123, signal.SIGKILL)]
    assert capsys.readouterr().err == "STT-ENGINES-TIMEOUT\n"


def test_checkout_cache_rejected(modules, monkeypatch):
    _, runtime = modules
    monkeypatch.setenv("HF_HOME", str(SERVICE / ".cache"))
    with pytest.raises(ValueError):
        runtime.cache_root("diarize")


def test_stale_pending_removed_only_with_exclusive_lock(backend, modules, wav, capsys):
    import fcntl

    runtime, _, _ = backend
    cli, _ = modules
    root = runtime.cache_root("diarize")
    pending = root / "pending"
    pending.mkdir(parents=True)
    (pending / "broken.incomplete").write_bytes(b"partial")
    args = cli.parse_args(["diarize", "--wav", str(wav)])
    with (root / "cache.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            runtime.run(args)
        assert (pending / "broken.incomplete").is_file()
    assert runtime.run(args) == 0
    assert not pending.exists()
    assert not (root / "ready" / "broken.incomplete").exists()
    capsys.readouterr()


def test_offline_cache_miss_is_not_online_fallback(backend, modules, monkeypatch):
    runtime, calls, _ = backend
    cli, _ = modules
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(FileNotFoundError):
        runtime.run(cli.parse_args(["prepare"]))
    assert calls == []


def test_gated_exit_five_and_error_text_redaction(modules, monkeypatch, capsys):
    _, runtime = modules
    monkeypatch.setenv("HF_TOKEN", "test-only-placeholder")
    monkeypatch.setattr(sys, "argv", ["stt-engines", "prepare"])

    class Gate(RuntimeError):
        response = SimpleNamespace(
            url="https://huggingface.co/pyannote/segmentation-3.0/resolve/main/model.bin",
            headers={"X-Error-Code": "GatedRepo"},
        )

    def fail(args):
        raise Gate("private text must not be printed")

    monkeypatch.setattr(runtime, "run", fail)
    assert runtime.main() == 5
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "STT-ENGINES-GATED https://huggingface.co/pyannote/segmentation-3.0\n"


@pytest.mark.parametrize("debug", [None, "0", "1"])
def test_error_debug_is_opt_in_private_and_keeps_default_bytes(backend, monkeypatch, capsys, debug):
    runtime, _, tmp_path = backend
    monkeypatch.delenv("STT_ENGINES_DEBUG", raising=False)
    if debug is not None:
        monkeypatch.setenv("STT_ENGINES_DEBUG", debug)
    monkeypatch.setattr(sys, "argv", ["stt-engines", "prepare", "--engine", "align"])
    secret = "private-exception-detail"

    def fail(*args):
        print(secret, file=sys.stderr)
        raise ValueError(secret) from RuntimeError("private-cause-detail")

    monkeypatch.setattr(runtime, "align", fail)
    assert runtime.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    files = list(tmp_path.rglob("*.log"))
    if debug != "1":
        assert output.err == "STT-ENGINES-CPU-ONLY\nSTT-ENGINES-ERROR ValueError\n"
        assert files == []
    else:
        assert len(files) == 1
        path = files[0]
        assert output.err == f"STT-ENGINES-CPU-ONLY\n{path}\nSTT-ENGINES-ERROR ValueError\n"
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent == tmp_path / "stt-engines-debug"
        assert not path.is_relative_to(ROOT)
        content = path.read_text(encoding="utf-8")
        assert "Traceback (most recent call last)" in content
        assert "ValueError: " + secret in content
        assert "RuntimeError: private-cause-detail" in content


@pytest.mark.parametrize("symlink", [False, True])
def test_debug_dump_refuses_checkout_without_disclosing_failure(modules, monkeypatch, tmp_path, capsys, symlink):
    _, runtime = modules
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setenv("STT_ENGINES_CHECKOUT_ROOT", str(checkout))
    monkeypatch.setenv("STT_ENGINES_DEBUG", "1")
    if symlink:
        (tmp_path / "stt-engines-debug").symlink_to(checkout, target_is_directory=True)
        monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    else:
        monkeypatch.setenv("HF_HOME", str(checkout / "hf"))
    runtime.debug_dump(ValueError("private-exception-detail"))
    assert capsys.readouterr().err == "STT-ENGINES-DEBUG-FAILED ValueError\n"
    assert list(checkout.iterdir()) == []


def test_debug_dump_io_failure_has_safe_sentinel(modules, monkeypatch, tmp_path, capsys):
    _, runtime = modules
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setenv("STT_ENGINES_DEBUG", "1")
    (tmp_path / "stt-engines-debug").write_text("not a directory", encoding="utf-8")
    runtime.debug_dump(ValueError("private-exception-detail"))
    assert capsys.readouterr().err == "STT-ENGINES-DEBUG-FAILED FileExistsError\n"


def test_download_has_no_backoff_or_stream_retry(modules, monkeypatch):
    from io import BytesIO

    _, runtime = modules
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size):
            yield b"partial"
            raise OSError("stream interrupted")

    def request(**kwargs):
        calls.append(kwargs)
        return Response()

    download = SimpleNamespace(http_backoff=request, hf_raise_for_status=lambda response: None)
    monkeypatch.setitem(sys.modules, "huggingface_hub.file_download", download)
    runtime.configure_downloads()
    with pytest.raises(OSError):
        download.http_get("https://example.invalid/model", BytesIO(), expected_size=100)
    assert len(calls) == 1
    assert calls[0]["max_retries"] == 0
    assert calls[0]["timeout"] == 30


def test_repeated_cancel_during_reap_does_not_leave_child(modules, monkeypatch, capsys):
    cli, _ = modules
    calls = []

    class Process:
        pid = 123

        def communicate(self, timeout=None):
            if timeout is not None:
                raise KeyboardInterrupt
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGTERM)
            calls.append("reaped")
            return "", ""

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append("killed"))
    assert cli.run_worker(["prepare"], 9) == 130
    assert calls == ["killed", "reaped"]
    assert capsys.readouterr().err == "STT-ENGINES-CANCELLED\n"


def test_provision_no_token_before_uv(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "HF_TOKEN"}
    env["PATH"] = str(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(SERVICE / "provision.sh")], env=env,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 4
    assert result.stderr == "STT-ENGINES-NO-TOKEN\n"
    assert result.stdout == ""


def test_provision_rejects_unexpanded_tilde_before_uv(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text("#!/bin/sh\nprintf unexpected-uv\nexit 77\n", encoding="utf-8")
    uv.chmod(0o755)
    env = dict(os.environ, HF_TOKEN="test-only-placeholder", PATH=f"{tools}:/usr/bin:/bin")
    result = subprocess.run(
        ["/bin/bash", str(SERVICE / "provision.sh"), "~/.venvs/stt-engines"],
        env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 1
    assert result.stderr == "STT-ENGINES-PATH-MUST-BE-ABSOLUTE\n"
    assert result.stdout == ""
