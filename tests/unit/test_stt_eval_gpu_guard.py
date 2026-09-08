"""실제 스텁 명령과 프로세스 간 lock으로 GPU 진입 계약을 검증한다."""
from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator, Mapping
from contextlib import chdir, contextmanager
from pathlib import Path
from typing import final
from unittest.mock import patch

from automation import pipeline_lock
from automation.stt_eval import gpu_guard


STUB = '''import os, sys
from pathlib import Path
if Path(sys.argv[0]).name == "nvidia-smi":
    assert sys.argv[1:] == ["--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"]
    print(os.environ.get("UTIL", "[N/A]"))
    sys.exit(int(os.environ.get("SMI_RC", "0")))
assert sys.argv[1:] in [["-x", "whisper-cli"], ["-f", "sherpa-onnx-offline-speaker-diarization"], ["-f", "bin/stt-engines"]]
if sys.argv[1] == "-f":
    print(os.environ["GUARD_PID"])
    print(os.environ["GUARD_PARENT"])
    if os.environ.get("PROCESS_BUSY"):
        print("99999999")
    sys.exit(0)
sys.exit(int(os.environ.get("PGREP_RC", "1")))
'''


def stub_environment(base: Path) -> dict[str, str]:
    binary = base / "bin"
    binary.mkdir()
    for name in ("nvidia-smi", "pgrep"):
        script = binary / name
        _ = script.write_text(f"#!{sys.executable}\n" + STUB)
        script.chmod(0o700)
    return {"PATH": str(binary), "HOME": str(base),
            "GUARD_PID": str(os.getpid()), "GUARD_PARENT": str(os.getppid())}


def child_command(code: str, *args: str) -> list[str]:
    """pytest 경로·cwd·환경 대신 파일에서 유도한 절대 루트로 시작한다."""
    root = Path(__file__).resolve().parents[2]
    bootstrap = f"import sys; sys.path.insert(0, {str(root)!r})\n"
    return [sys.executable, "-I", "-c", bootstrap + code, *args]


@contextmanager
def occupied_lock(env: Mapping[str, str]) -> Iterator[None]:
    code = ("from automation import pipeline_lock\n"
            "with pipeline_lock.hold() as acquired:\n"
            "    assert acquired\n"
            "    print('ready', flush=True)\n"
            "    sys.stdin.buffer.read()\n")
    with subprocess.Popen(child_command(code), env=dict(env), cwd=env["HOME"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        try:
            assert process.stdout is not None
            assert select.select([process.stdout], [], [], 5)[0], "lock owner ready timeout"
            assert process.stdout.readline() == b"ready\n", "lock owner not ready"
            yield
        finally:
            # 바이트를 쓰지 않고 EOF로 해제하므로 먼저 죽은 자식에도 BrokenPipe가 없다.
            try:
                _, error = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _ = process.communicate()
                raise AssertionError("lock owner cleanup timeout") from None
            assert process.returncode == 0, f"lock owner rc={process.returncode}: {error.decode()}"


@contextmanager
def live_long_name(base: Path, env: Mapping[str, str]) -> Iterator[dict[str, str]]:
    """실제 pgrep과 준비 신호를 쓰며 GPU·전사 엔진은 실행하지 않는다."""
    pgrep = shutil.which("pgrep")
    assert pgrep is not None, "pgrep required"
    (base / "bin/pgrep").unlink()
    (base / "bin/pgrep").symlink_to(pgrep)
    name = "sherpa-onnx-offline-speaker-diarization"
    script = base / name
    _ = script.write_text(
        "import ctypes, sys\n"
        "from pathlib import Path\n"
        "assert ctypes.CDLL(None).prctl(15, Path(sys.argv[0]).name.encode(), 0, 0, 0) == 0\n"
        "print('ready', flush=True)\nsys.stdin.buffer.read()\n"
    )
    with subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            assert process.stdout is not None
            assert select.select([process.stdout], [], [], 5)[0], "process ready timeout"
            assert process.stdout.readline() == b"ready\n"
            comm = Path(f"/proc/{process.pid}/comm").read_text().strip()
            assert comm == name[:15]
            exact = subprocess.run([pgrep, "-x", name], capture_output=True, text=True,
                                   check=False, timeout=5)
            assert exact.returncode == 1
            print(f"synthetic_process comm={comm} exact_pgrep_rc={exact.returncode}")
            yield dict(env)
        finally:
            try:
                _, error = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _ = process.communicate()
                raise AssertionError("synthetic process cleanup timeout") from None
            assert process.returncode == 0, error.decode()


def cleanup_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def interrupt_when_ready(argv: list[str], fifo: Path) -> tuple[int, int]:
    os.mkfifo(fifo, 0o600)
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    try:
        with subprocess.Popen(argv, cwd=fifo.parent, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as process:
            try:
                assert select.select([fd], [], [], 5)[0], "CLI ready timeout"
                pid = int(os.read(fd, 128))
                process.send_signal(signal.SIGTERM)
                process.send_signal(signal.SIGTERM)
                return process.wait(timeout=5), pid
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        _ = process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        _ = process.wait()
    finally:
        os.close(fd)
        fifo.unlink(missing_ok=True)


@final
class GuardTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.workspace = tempfile.TemporaryDirectory()
        self.base = Path(self.workspace.name)
        self.env = stub_environment(self.base)
        self.addCleanup(self.workspace.cleanup)

    def test_na_excludes_self_and_parent_and_holds_existing_lock(self) -> None:
        with gpu_guard.check(self.env) as verdict:
            self.assertEqual(verdict.exit_code, 0)
            with pipeline_lock.hold(self.env) as acquired:
                self.assertFalse(acquired)
        with pipeline_lock.hold(self.env) as acquired:
            self.assertTrue(acquired)
        self.assertTrue(pipeline_lock.lock_path(self.env).is_file())

    def test_numeric_threshold_all_devices_and_malformed_output(self) -> None:
        for util, code in [("0", 0), ("4.9", 0), ("5", 6), ("90", 6),
                           ("0\n6", 6), ("garbage", 6), ("nan", 6), ("-1", 6)]:
            with self.subTest(util=util), gpu_guard.check({**self.env, "UTIL": util}) as verdict:
                self.assertEqual(verdict.exit_code, code)

    def test_process_busy(self) -> None:
        with gpu_guard.check({**self.env, "PROCESS_BUSY": "1"}) as verdict:
            self.assertEqual((verdict.exit_code, verdict.reason), (6, "process"))

    def test_live_long_name_process_is_busy(self) -> None:
        with live_long_name(self.base, self.env) as env:
            with gpu_guard.check(env) as verdict:
                print(f"live_long_name exit={verdict.exit_code} reason={verdict.reason or 'idle'}")
                self.assertEqual((verdict.exit_code, verdict.reason), (6, "process"))

    def test_lock_busy(self) -> None:
        with occupied_lock(self.env), gpu_guard.check(self.env) as verdict:
            self.assertEqual((verdict.exit_code, verdict.reason), (6, "lock"))

    def test_probe_errors_fail_closed(self) -> None:
        for extra in ({"SMI_RC": "1"}, {"PGREP_RC": "2"}, {"PATH": "/nonexistent"}):
            with self.subTest(extra=extra), gpu_guard.check({**self.env, **extra}) as verdict:
                self.assertEqual(verdict.exit_code, 6)

    def test_lock_child_ignores_parent_import_path_and_cwd(self) -> None:
        root = Path(__file__).resolve().parents[2]
        isolated = [p for p in sys.path if p and not Path(p).resolve().is_relative_to(root)]
        with chdir(self.base), patch.object(sys, "path", isolated):
            with occupied_lock(self.env):
                with pipeline_lock.hold(self.env) as acquired:
                    self.assertFalse(acquired)
        with pipeline_lock.hold(self.env) as acquired:
            self.assertTrue(acquired)

    def test_lock_startup_failure_reaps_child_without_broken_pipe(self) -> None:
        process = subprocess.Popen(child_command("raise SystemExit(23)"),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with patch.object(subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(AssertionError, "lock owner rc=23"):
                with occupied_lock(self.env):
                    self.fail("failed child acquired lock")
        self.assertEqual(process.returncode, 23)
        with self.assertRaises(ChildProcessError):
            _ = os.waitpid(process.pid, os.WNOHANG)
        self.assertTrue(all(stream is not None and stream.closed for stream in
                            (process.stdin, process.stdout, process.stderr)))

    def test_failed_fifo_readiness_reaps_child_and_removes_fifo(self) -> None:
        fifo, receipt = self.base / "ready", self.base / "pid"
        code = ("import os, signal\nfrom pathlib import Path\n"
                "Path(sys.argv[2]).write_text(str(os.getpid()))\n"
                "Path(sys.argv[1]).write_text('invalid')\nsignal.pause()\n")
        with self.assertRaises(ValueError):
            _ = interrupt_when_ready(child_command(code, str(fifo), str(receipt)), fifo)
        pid = int(receipt.read_text())
        with self.assertRaises(ChildProcessError):
            _ = os.waitpid(pid, os.WNOHANG)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse(fifo.exists())
        receipt.unlink()
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["bin"])

    def test_hung_probe_is_bounded(self) -> None:
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 1)):
            with gpu_guard.check(self.env) as verdict:
                self.assertEqual(verdict.exit_code, 6)
