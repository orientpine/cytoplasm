"""기존 스냅샷 계약과 선택 출처 필드의 경계를 검증한다."""
from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from automation.stt_eval import model, snapshot


def test_characterize_legacy_record_and_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    record = model.EvalRecord("fixture", "a" * 64, 1000, "fixture")
    model.dump_record(record, path)
    raw = cast(dict[str, object], json.loads(path.read_text()))
    _ = raw.pop("provenance", None)
    _ = path.write_text(json.dumps(raw))
    assert model.load_record(path) == record
    raw["unexpected"] = "fixture"
    _ = path.write_text(json.dumps(raw))
    with pytest.raises(model.EvalRecordError, match="unknown"):
        _ = model.load_record(path)


def test_characterize_snapshot_move_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "fixture.eval.json"
    record = model.EvalRecord("fixture", "a" * 64, 1000, "fixture", config_sha256="b" * 64)
    model.dump_record(record, source)
    before = source.read_bytes()
    env = {"STT_EVAL_ROOT": str(tmp_path / "eval")}
    target = snapshot.move_snapshot(source.with_suffix(".md"), env)
    assert target is None
    target = snapshot.move_snapshot(tmp_path / "fixture.md", env)
    assert target is not None and target.read_bytes() == before
    assert not source.exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    model.dump_record(record, source)

    def refuse(_source: Path, _target: Path) -> Path:
        raise OSError("fixture")

    monkeypatch.setattr(Path, "replace", refuse)
    assert snapshot.move_snapshot(tmp_path / "fixture.md", env) is None
    assert source.read_bytes() == before
    assert target.read_bytes() == before
    assert not list(target.parent.glob(".snapshot-*"))
    assert capsys.readouterr().err == "STT-EVAL-SNAPSHOT-FAIL reason=OSError\n"


def test_reference_provenance_roundtrip(tmp_path: Path) -> None:
    record = model.EvalRecord("fixture", "a" * 64, 1000, "fixture")
    path = tmp_path / "record.json"
    model.dump_record(record, path)
    raw = cast(dict[str, object], json.loads(path.read_text()))
    raw["provenance"] = "owner-edit:lifelog:2026-09-07T03:40:00+09:00"
    _ = path.write_text(json.dumps(raw))
    loaded = model.load_record(path)
    assert loaded.provenance == raw["provenance"]
    target = snapshot.write_reference(loaded, {"STT_EVAL_ROOT": str(tmp_path / "eval")})
    assert target is not None
    assert target == tmp_path / "eval" / "reference" / (("a" * 64) + ".json")
    assert model.load_record(target) == loaded
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert not list((tmp_path / "eval").glob("hyp"))
    assert not (tmp_path / "eval" / "manifest.jsonl").exists()


@pytest.mark.parametrize("value", ["", 4, "owner-edit:discord:2026-09-07T00:00:00Z", "owner-edit:meeting:bad", "owner-edit:meeting:2026-02-30T00:00:00Z", "owner-edit:meeting:2026-09-07"])
def test_invalid_provenance_fails_closed(tmp_path: Path, value: object) -> None:
    path = tmp_path / "record.json"
    model.dump_record(model.EvalRecord("fixture", "a" * 64, 0, ""), path)
    raw = cast(dict[str, object], json.loads(path.read_text()))
    raw["provenance"] = value
    _ = path.write_text(json.dumps(raw))
    with pytest.raises(model.EvalRecordError, match="provenance"):
        _ = model.load_record(path)


def test_reference_failure_preserves_previous_and_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    record = model.EvalRecord("fixture", "a" * 64, 1000, "fixture", provenance="owner-edit:meeting:2026-09-07T00:00:00Z")
    env = {"STT_EVAL_ROOT": str(tmp_path / "eval")}
    target = snapshot.write_reference(record, env)
    assert target is not None
    before = target.read_bytes()
    original = Path.replace

    def refuse(_source: Path, _target: Path) -> Path:
        raise OSError("fixture")

    monkeypatch.setattr(Path, "replace", refuse)
    assert snapshot.write_reference(replace(record, text="updated"), env) is None
    assert target.read_bytes() == before
    assert not list(target.parent.glob(".snapshot-*"))
    assert "STT-EVAL-SNAPSHOT-FAIL reason=OSError" in capsys.readouterr().err
    monkeypatch.setattr(Path, "replace", original)
    assert snapshot.write_reference(replace(record, text="updated"), env) == target
    assert model.load_record(target).text == "updated"


def test_reference_refuses_checkout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".git").mkdir()
    record = model.EvalRecord("fixture", "a" * 64, 0, "", provenance="owner-edit:lifelog:2026-09-07T00:00:00Z")
    assert snapshot.write_reference(record, {"STT_EVAL_ROOT": str(tmp_path)}) is None
    assert capsys.readouterr().err == "STT-EVAL-ROOT-REFUSED\n"
    assert not (tmp_path / "reference").exists()
