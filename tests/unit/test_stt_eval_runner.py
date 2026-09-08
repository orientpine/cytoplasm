"""가짜 Drive와 실제 자식 CLI로 순차 실행과 실패 원장을 검증한다."""
from __future__ import annotations

import hashlib
import io
import inspect
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import cast, final
from unittest.mock import patch

from automation.drive_client import DriveClientError
from automation.stt_eval import runner
from automation.stt_eval.model import load_record
from automation.stt_eval.runner_inputs import Configs, Manifest, read_jsonl
from tests.unit.test_stt_eval_gpu_guard import child_command, cleanup_group, interrupt_when_ready, occupied_lock, stub_environment


CLI = '''import fcntl, hashlib, json, os, signal, sys, time
from pathlib import Path
args = sys.argv[1:]
assert args[0:2] == ["transcribe", "--file"]
audio = Path(args[2])
assert args[3:] == ["--label", "eval-" + hashlib.sha256(audio.read_bytes()).hexdigest()[:8]]
assert os.environ["CREDENTIAL_SENTINEL"] == "forwarded"
assert os.environ["SPEECHTOTEXT_BACKEND"] == "local"
assert os.environ["DRIVE_PUBLISH_ENABLED"] == "0"
work = Path(os.environ["SPEECHTOTEXT_TRANSCRIPT_DIR"])
assert work.stat().st_mode & 0o777 == 0o700
assert Path(os.environ["SPEECHTOTEXT_WINDOW_CACHE"]).is_relative_to(work)
active = Path(os.environ["ACTIVE"])
active_file = active.open("w")
fcntl.flock(active_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
with Path(os.environ["CALLS"]).open("a") as f:
    f.write(json.dumps({"audio": str(audio), "prompt": os.environ.get("SPEECHTOTEXT_PROMPT", "")}) + "\\n")
mode = os.environ.get("SPEECHTOTEXT_TEST_MODE", "ok")
if mode == "pause":
    with open(os.environ["READY_FIFO"], "w") as ready:
        ready.write(str(os.getpid()))
    signal.pause()
if mode == "sleep":
    time.sleep(60)
if mode != "absent":
    record = dict(schema="stt-eval/v1", recording_id="synthetic", audio_sha256=hashlib.sha256(audio.read_bytes()).hexdigest(), duration_ms=1000, text="synthetic", words=[], turns=[], text_regions=[], der_regions=[], entities=[], gaps=[], status="ok", config_sha256=os.environ["SPEECHTOTEXT_TEST_HASH"])
    if mode == "drift":
        record["config_sha256"] = hashlib.sha256(audio.read_bytes()).hexdigest()
    if mode == "wrong-audio":
        record["audio_sha256"] = "0" * 64
    (work / "result.eval.json").write_text("" if mode == "empty" else json.dumps(record))
active_file.close()
sys.exit(7 if mode == "rc" else 0)
'''


@final
class FakeDrive:
    def __init__(self) -> None:
        self.payloads = {"first": b"synthetic-a", "second": b"synthetic-b"}
        self.checked: list[str] = []
        self.downloads: list[Path] = []
        self.interrupt = False

    def verify_owner_only(self, file_id: str) -> None:
        self.checked.append(file_id)

    def download_file(self, file_id: str, dest: Path, *, export_as: str = "") -> str:
        assert not export_as
        assert self.checked[-1] == file_id
        assert dest.parent.stat().st_mode & 0o777 == 0o700
        self.downloads.append(dest)
        _ = dest.write_bytes(self.payloads[file_id])
        if self.interrupt and file_id == "second":
            raise KeyboardInterrupt
        return "untrusted-checksum"


@final
class RunnerTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.workspace = tempfile.TemporaryDirectory()
        self.base = Path(self.workspace.name)
        self.addCleanup(self.workspace.cleanup)
        self.env = {**stub_environment(self.base), "CREDENTIAL_SENTINEL": "forwarded",
                    "CALLS": str(self.base / "calls"), "ACTIVE": str(self.base / "active")}
        self.root, self.tmp = self.base / "eval", self.base / "tmp"
        self.cli = self.base / "fake_cli.py"
        _ = self.cli.write_text(CLI)
        model = self.base / "model.bin"
        _ = model.write_bytes(b"synthetic-model")
        self.configs = tuple(runner.Candidate(label, (("SPEECHTOTEXT_WHISPER_MODEL", str(model)),
                        ("SPEECHTOTEXT_TEST_HASH", digit * 64))) for label, digit in (("a", "a"), ("b", "b")))
        self.drive = FakeDrive()
        self.manifest = [{"drive_file_id": key, "audio_sha256": hashlib.sha256(value).hexdigest(),
                          "duration_ms": 1000} for key, value in self.drive.payloads.items()]

    def execute(self, *, manifest: Manifest | None = None, configs: Configs | None = None,
                root: Path | None = None) -> int:
        return runner.run(self.manifest if manifest is None else manifest,
                          self.configs if configs is None else configs,
                          self.root if root is None else root, drive=self.drive, cli=self.cli,
                          tmp=self.tmp, env=self.env)

    def rows(self, name: str = "runs") -> list[dict[str, object]]:
        path = self.root / f"{name}.jsonl"
        return read_jsonl(path) if path.exists() else []

    def mode(self, mode: str) -> None:
        overrides = dict(self.configs[0].env_overrides)
        overrides["SPEECHTOTEXT_TEST_MODE"] = mode
        self.configs = (runner.Candidate("a", tuple(overrides.items())),)

    def test_happy_sequential_resume_permissions(self) -> None:
        configs, manifest = self.base / "configs.json", self.base / "manifest.jsonl"
        _ = configs.write_text(json.dumps([{"label": c.label, "env_overrides": dict(c.env_overrides)} for c in self.configs]))
        original = "".join(json.dumps(row) + "\n" for row in self.manifest)
        _ = manifest.write_text(original)
        self.assertEqual(self.execute(configs=configs, manifest=manifest), 0)
        self.assertEqual(manifest.read_text(), original)
        self.assertEqual([row["status"] for row in self.rows()], ["ok"] * 4)
        self.assertEqual([row["label"] for row in self.rows()], ["a", "b", "a", "b"])
        self.assertEqual(len(self.rows("candidates")), 2)
        artifacts = list(self.root.glob("hyp/*/*.json"))
        self.assertEqual(len(artifacts), 4)
        for path in artifacts:
            self.assertEqual(load_record(path).config_sha256, path.parent.name)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.tmp.iterdir()), [])
        calls = Path(self.env["CALLS"]).read_bytes()
        self.assertEqual(self.execute(), 0)
        self.assertEqual(Path(self.env["CALLS"]).read_bytes(), calls)
        self.assertEqual(len(self.rows()), 4)
        self.assertTrue(all(len(line) <= 4096 for line in (self.root / "runs.jsonl").read_bytes().splitlines()))

    def test_busy_paths_start_no_cli_or_drive(self) -> None:
        for extra, reason in [({"UTIL": "5"}, "utilization"), ({"PROCESS_BUSY": "1"}, "process")]:
            self.env.update(extra)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(self.execute(), 6)
            self.assertIn(f"GPU-BUSY {reason}", output.getvalue())
            _ = self.env.pop(next(iter(extra)))
        with occupied_lock(self.env), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.execute(), 6)
        self.assertIn("GPU-BUSY lock", output.getvalue())
        self.assertEqual(self.drive.downloads, [])
        self.assertFalse(Path(self.env["CALLS"]).exists())

    def test_mismatch_missing_and_false_success(self) -> None:
        for mode, reason in [("empty", "invalid-artifact"), ("absent", "no-artifact"),
                             ("wrong-audio", "artifact-mismatch"), ("rc", "cli-failed")]:
            self.mode(mode)
            self.assertEqual(self.execute(), 1)
            self.assertEqual(self.rows()[-1]["reason"], reason)
        self.drive.payloads["first"] = b"changed"
        self.assertEqual(self.execute(manifest=self.manifest[:1]), 1)
        self.assertEqual(self.rows()[-1]["reason"], "audio-mismatch")
        self.assertEqual(list(self.root.glob("hyp/*/*.json")), [])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_model_missing_never_calls_cli(self) -> None:
        self.configs = (runner.Candidate("missing", (("SPEECHTOTEXT_WHISPER_MODEL", "/missing"),)),)
        self.assertEqual(self.execute(), 1)
        self.assertEqual([row["reason"] for row in self.rows()], ["model-missing"] * 2)
        self.assertTrue(all(row["config_sha256"] is None for row in self.rows()))
        self.assertFalse(Path(self.env["CALLS"]).exists())

    def test_timeout_reaps_child_and_cleans_tmp(self) -> None:
        self.mode("sleep")
        self.env["STT_EVAL_TIMEOUT_FACTOR"] = "0"
        with patch.object(runner, "MIN_TIMEOUT_SECS", 0.15):
            self.assertEqual(self.execute(manifest=self.manifest[:1]), 1)
        self.assertEqual(self.rows()[0]["reason"], "timeout")
        self.assertIsNone(self.rows()[0]["config_sha256"])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_drift_fails_without_second_artifact(self) -> None:
        self.mode("drift")
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.rows()[-1]["reason"], "CANDIDATE-DRIFT")
        self.assertEqual(len(list(self.root.glob("hyp/*/*.json"))), 1)

    def test_interrupt_resume_and_unique_temp_paths(self) -> None:
        self.drive.interrupt = True
        for _ in range(2):
            with self.assertRaises(KeyboardInterrupt):
                _ = self.execute()
            self.assertEqual(len(self.rows()), 2)
            self.assertEqual(list(self.tmp.iterdir()), [])
        self.drive.interrupt = False
        self.assertEqual(self.execute(), 0)
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(len(set(self.drive.downloads)), len(self.drive.downloads))

    def test_unjournaled_stale_hyp_is_replaced(self) -> None:
        target = self.root / "hyp" / ("a" * 64) / f"{self.manifest[0]['audio_sha256']}.json"
        target.parent.mkdir(parents=True)
        _ = target.write_text("stale")
        self.assertEqual(self.execute(), 0)
        self.assertEqual(load_record(target).status, "ok")

    def test_malformed_input_checkout_symlink_and_shell_text(self) -> None:
        bad = self.base / "bad.json"
        for content in ("{", '[{"label":"x","env_overrides":{"PATH":"bad"}}]'):
            _ = bad.write_text(content)
            self.assertEqual(self.execute(configs=bad), 2)
        invalid = [{**self.manifest[0], "audio_sha256": "../bad"}]
        self.assertEqual(self.execute(manifest=invalid), 2)
        checkout = self.base / "checkout"
        checkout.mkdir()
        _ = (checkout / ".git").write_text("gitdir: elsewhere")
        link = self.base / "alias"
        link.symlink_to(checkout, target_is_directory=True)
        self.assertEqual(self.execute(root=link / "state"), 3)
        self.assertFalse(self.root.exists())
        self.configs = (runner.Candidate("a", (*self.configs[0].env_overrides,
                         ("SPEECHTOTEXT_PROMPT", "$(touch should-not-exist); ignore instructions"))),)
        self.assertEqual(self.execute(), 0)
        self.assertFalse((self.base / "should-not-exist").exists())
        self.assertIn("$(touch", cast(str, read_jsonl(Path(self.env["CALLS"]))[0]["prompt"]))

    def test_real_sigterm_reaps_cli_and_resumes_finished_rows(self) -> None:
        self.assertEqual(self.execute(manifest=self.manifest[:1]), 0)
        fifo = self.base / "ready"
        payload = {"env": {**self.env, "READY_FIFO": str(fifo), "SPEECHTOTEXT_TEST_MODE": "pause"},
                   "root": str(self.root), "tmp": str(self.tmp), "cli": str(self.cli),
                   "manifest": self.manifest, "configs": [{"label": c.label, "env_overrides": dict(c.env_overrides)} for c in self.configs]}
        path = self.base / "child.json"
        _ = path.write_text(json.dumps(payload))
        code = ("import json, os\nfrom pathlib import Path\nfrom typing import final\n"
                + inspect.getsource(FakeDrive)
                + "\nfrom automation.stt_eval.runner import run; p=json.loads(Path(sys.argv[1]).read_text()); p['env'].update(GUARD_PID=str(os.getpid()),GUARD_PARENT=str(os.getppid())); c=Path(sys.argv[1]).with_suffix('.configs'); c.write_text(json.dumps(p['configs'])); sys.exit(run(p['manifest'],c,Path(p['root']),drive=FakeDrive(),cli=Path(p['cli']),tmp=Path(p['tmp']),env=p['env']))")
        rc, pid = interrupt_when_ready(child_command(code, str(path)), fifo)
        self.addCleanup(cleanup_group, pid)
        self.assertFalse(fifo.exists())
        self.assertNotEqual(rc, 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.execute(), 0)
        self.assertEqual(len(self.rows()), 4)

    def test_relative_paths_survive_private_child_cwd(self) -> None:
        self.cli = Path(os.path.relpath(self.cli))
        overrides = dict(self.configs[0].env_overrides)
        overrides["SPEECHTOTEXT_WHISPER_MODEL"] = os.path.relpath(self.base / "model.bin")
        self.configs = (runner.Candidate("a", tuple(overrides.items())),)
        self.assertEqual(self.execute(), 0)

    def test_drive_failure_is_journaled_and_continues(self) -> None:
        with patch.object(self.drive, "verify_owner_only", side_effect=DriveClientError("private")):
            self.assertEqual(self.execute(), 1)
        self.assertEqual([row["reason"] for row in self.rows()], ["drive-failed"] * 4)
        self.assertEqual(list(self.tmp.iterdir()), [])
