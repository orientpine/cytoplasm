"""Procurement review uses the shared Drive publishing facade."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "procurement" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import procure_review as pr  # noqa: E402


def test_drive_link_publishes_with_facade_and_uses_first_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "대형-용역요청서.hwpx"
    target.write_text("x", encoding="utf-8")
    calls: list[tuple[str, str, list[tuple[Path, str]]]] = []

    def fake_publish(kind: str, title: str, artifacts: list[tuple[Path, str]]):
        calls.append((kind, title, artifacts))
        return SimpleNamespace(links=("https://drive.example/file-1",))

    import automation.drive_outputs as outputs

    monkeypatch.setattr(outputs, "publish_best_effort", fake_publish)
    monkeypatch.setenv("PROCURE_DM_MAX_BYTES", "0")
    stub = tmp_path / "stub"
    stub.mkdir()
    monkeypatch.setenv("PROCURE_DISCORD_STUB", str(stub))

    result = pr.send_review(target, "검토")

    assert "mode=drive-link" in result
    record = json.loads(next(stub.iterdir()).read_text(encoding="utf-8"))
    assert "https://drive.example/file-1" in record["content"]
    assert calls == [("procurement", target.stem, [(target, target.stem)])]


def test_drive_publish_failure_keeps_empty_link_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "large.hwpx"
    target.write_text("x", encoding="utf-8")

    import automation.drive_outputs as outputs

    monkeypatch.setattr(outputs, "publish_best_effort", lambda *args, **kwargs: None)
    monkeypatch.setenv("PROCURE_DM_MAX_BYTES", "0")
    stub = tmp_path / "stub"
    stub.mkdir()
    monkeypatch.setenv("PROCURE_DISCORD_STUB", str(stub))

    result = pr.send_review(target, "검토")

    record = json.loads(next(stub.iterdir()).read_text(encoding="utf-8"))
    assert "mode=drive-link" in result
    assert "(Drive 링크: )" in record["content"]
