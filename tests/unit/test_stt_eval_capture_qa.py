"""실제 --once 래퍼를 두 번 실행하는 재현 가능한 오프라인 수동 QA."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.unit.test_stt_eval_capture import BODY, LEGEND, WRAPPER, fixture, note, references
from tests.unit.test_stt_eval_capture_deploy import executable

from automation.plaud_sync.audio_manifest import ManifestEntry, record_manifest
from automation.stt_eval.model import load_record


def manual_qa(root: Path) -> None:
    env, edited = fixture(root)
    _ = edited.write_text(note(BODY.replace("가상", "수정")))
    local = root / ".hermes/speechtotext/transcripts/meeting-fixture.md"
    local.parent.mkdir(parents=True)
    markdown = "# fixture\n\n" + LEGEND + "\n\n---\n\n" + BODY
    _ = local.write_text(markdown)
    remote = root / "drive-owner-edit.md"
    _ = remote.write_text(markdown.replace("가상", "수정"))
    _ = record_manifest(ManifestEntry("c" * 64, "fixture-audio", 2000, local.stem, "2026-09-07T00:00:00Z", "meeting"), env)
    gws = executable(root / "gws", (
        "import json, os, sys\nfrom pathlib import Path\n"
        "args = sys.argv\nassert args[1:3] == ['drive', 'files']\n"
        "if args[3] == 'get':\n"
        "    sys.stdout.write(Path(os.environ['FIXTURE_REMOTE']).read_text())\n"
        "else:\n"
        "    assert args[3] == 'list'\n"
        "    q = json.loads(args[args.index('--params') + 1])['q']\n"
        "    mime = 'application/vnd.google-apps.folder'\n"
        "    if 'name =' in q: rows = [{'id': 'transcripts'}]\n"
        "    elif \"'transcripts'\" in q: rows = [{'id': 'project', 'name': 'fixture', 'mimeType': mime}]\n"
        "    elif \"'project'\" in q: rows = [{'id': 'year', 'name': '2026', 'mimeType': mime}]\n"
        "    else:\n"
        "        assert \"'year'\" in q\n"
        "        rows = [{'id': 'file', 'name': 'meeting-fixture.md', 'modifiedTime': '2026-09-07T00:00:00Z'}]\n"
        "    print(json.dumps({'files': rows}))\n"
    ))
    env.update(DRIVE_PUBLISH_ENABLED="1", DRIVE_GWS_BIN=str(gws), FIXTURE_REMOTE=str(remote))
    manifest = Path(env["STT_EVAL_ROOT"]) / "manifest.jsonl"
    original = manifest.read_bytes()
    saved: dict[str, bytes] = {}
    for tick in (1, 2):
        result = subprocess.run([sys.executable, str(WRAPPER), "--once"], cwd=root, env={**os.environ, **env}, capture_output=True, text=True, timeout=30, check=False)
        print(f"RUN {tick}: --once rc={result.returncode}")
        print(f"stdout={result.stdout!r}")
        print(f"stderr={result.stderr!r}")
        assert result.returncode == 0 and result.stderr == ""
        assert result.stdout == ("STT-EVAL-CAPTURE new=2 updated=0 skipped=0\n" if tick == 1 else "")
        paths = sorted(references(env))
        assert len(paths) == 2
        for path in paths:
            record = load_record(path)
            assert record.text.startswith("수정") and record.words[0].tag.speakers == ("화자1",)
            assert record.provenance is not None
            print(f"reference/{path.stem[:8]}...json exists=True edited=True label-preserved=True mode={path.stat().st_mode & 0o777:04o}")
        current = {path.name: path.read_bytes() for path in paths}
        if tick == 2:
            assert current == saved
            print("duplicate-records=0 reference-count=2 bytes-unchanged=True")
        saved = current
    assert manifest.read_bytes() == original and local.read_text() == markdown
    print("manifest-unchanged=True frozen-transcript-unchanged=True")
    edited.unlink()
    empty_home = root / "missing-mirror"
    empty_home.mkdir()
    env.update(HOME=str(empty_home), STT_EVAL_ROOT=str(empty_home / "eval"), DRIVE_PUBLISH_ENABLED="0")
    result = subprocess.run([sys.executable, str(WRAPPER), "--once"], cwd=root, env={**os.environ, **env}, capture_output=True, text=True, timeout=30, check=False)
    print(f"MISSING MIRROR: rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}")
    assert result.returncode == 0 and result.stdout == "STT-EVAL-CAPTURE-SKIP reason=mirror-missing\n"


def test_real_wrapper_two_surface_qa(tmp_path: Path) -> None:
    manual_qa(tmp_path)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="stt-capture-manual-") as directory:
        manual_qa(Path(directory))
    print("CLEANUP fixture-directory-removed=True network=0 node-deploy=0")
