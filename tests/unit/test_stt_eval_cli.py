"""실제 모듈 CLI로 루트 경계와 마스킹된 저장·출력을 검증한다."""
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from automation.stt_eval.compare_store import JSON
from automation.stt_eval.model import EvalRecord, EvalWord, SpeakerTag, dump_record

REPO = Path(__file__).resolve().parents[2]
A, B = "a" * 64, "b" * 64


def obj(value: JSON) -> dict[str, JSON]:
    assert isinstance(value, dict)
    return value


def arr(value: JSON) -> list[JSON]:
    assert isinstance(value, list)
    return value


def parsed(text: str) -> dict[str, JSON]:
    return obj(cast(JSON, json.loads(text)))


def cli(*args: str | Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "automation.stt_eval", *map(str, args)],
                          cwd=REPO, text=True, capture_output=True, timeout=30, env=env)


def populate(root: Path) -> None:
    for folder in (root / "reference", root / "hyp" / A, root / "hyp" / B):
        folder.mkdir(parents=True)
    rows: list[dict[str, JSON]] = []
    for i in range(3):
        sha = f"{i + 1:08x}" + "0" * 56
        ref = EvalRecord("SECRET-ID", sha, 1000, "비공개 발화",
                         words=(EvalWord("SECRET-WORD", 0, 6, None, None,
                                         SpeakerTag("SPEAKER", ("SECRET-SPEAKER",)), "missing"),))
        dump_record(ref, root / "reference" / f"{sha}.json")
        for digest in (A, B):
            dump_record(replace(ref, config_sha256=digest), root / "hyp" / digest / f"{sha}.json")
        rows.append({"audio_sha256": sha, "domain": "meeting" if i < 2 else "lifelog",
                     "recording_id": "SECRET-ID", "drive_file_id": "SECRET-DRIVE", "duration_ms": 1000})
    _ = (root / "manifest.jsonl").write_text("\n".join(map(json.dumps, rows)))
    _ = (root / "candidates.jsonl").write_text("\n".join(json.dumps({"label": label, "config_sha256": sha})
                                                    for label, sha in (("baseline", A), ("candidate", B))))


def test_empty_status_and_env_default(tmp_path: Path) -> None:
    for args, env in [(("--root", tmp_path / "absent"), None),
                      ((), dict(os.environ, STT_EVAL_ROOT=str(tmp_path)))]:
        result = cli("status", *args, env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "no records"
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("command", ["status", "evaluate", "compare"])
def test_checkout_root_refused(tmp_path: Path, command: str) -> None:
    link = tmp_path / "checkout"
    link.symlink_to(REPO, target_is_directory=True)
    extra = ("--a", A, "--b", B) if command == "compare" else ()
    result = cli(command, "--root", link / "nested", *extra)
    assert result.returncode == 3
    assert "STT-EVAL-ROOT-REFUSED" in result.stderr


def test_evaluate_reports_and_status_are_masked(tmp_path: Path) -> None:
    populate(tmp_path)
    result = cli("evaluate", "--root", tmp_path, "--config", A, "--json")
    assert result.returncode == 0, result.stderr
    payload = parsed(result.stdout)
    assert payload["n"] == 3
    assert obj(payload["micro"])["cer"] == obj(payload["micro"])["cpcer_strict"] == 0
    assert [obj(row)["recording_id"] for row in arr(payload["records"])] == ["00000001", "00000002", "00000003"]
    reports = list((tmp_path / "reports").glob("*.json"))
    markdown = list((tmp_path / "reports").glob("*.md"))
    assert len(reports) == len(markdown) == 1
    assert parsed(reports[0].read_text()) == payload
    visible = result.stdout + markdown[0].read_text()
    for secret in ("SECRET", "비공개", str(tmp_path), A, B):
        assert secret not in visible
    status = cli("status", "--root", tmp_path, "--json")
    assert status.returncode == 0, status.stderr
    counts = parsed(status.stdout)
    assert counts["records"] == counts["references"] == 3
    assert counts["hypotheses"] == {A[:8]: 3, B[:8]: 3}


def test_evaluate_without_config_and_label_compare(tmp_path: Path) -> None:
    populate(tmp_path)
    evaluated = cli("evaluate", "--root", tmp_path, "--json")
    assert evaluated.returncode == 0, evaluated.stderr
    assert len(arr(parsed(evaluated.stdout)["evaluations"])) == 2
    result = cli("compare", "--root", tmp_path, "--a", "baseline", "--b", "candidate",
                 "--metric", "cpcer_strict", "--json")
    assert result.returncode == 0, result.stderr
    compared = parsed(result.stdout)
    assert compared["n"] == 3 and obj(compared["micro"])["delta"] == 0
    assert obj(obj(compared["domains"])["meeting"])["n"] == 2
    assert obj(obj(compared["domains"])["lifelog"])["n"] == 1
    assert "baseline" not in result.stdout


def test_corrupt_json_fails_without_private_path(tmp_path: Path) -> None:
    populate(tmp_path)
    _ = next((tmp_path / "hyp" / A).glob("*.json")).write_text("{")
    result = cli("evaluate", "--root", tmp_path, "--config", A, "--json")
    assert result.returncode == 2
    assert "STT-EVAL-INPUT-ERROR" in result.stderr
    assert str(tmp_path) not in result.stderr
    assert not (tmp_path / "reports").exists()
