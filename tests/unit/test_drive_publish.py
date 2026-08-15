"""Shared drive-publish helper: final deliverables land in a 4-level project folder.

`<DRIVE_OUTPUTS_ROOT> / <doc_type> / <YYYY-MM> / <file>` — verified against the
vendored report copy (all three copies are byte-identical). ``publish_best_effort``
is opt-in so tests never make real Drive calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "report" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import drive_publish as dp  # noqa: E402


def test_publish_targets_root_type_period_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", "Autophagy 산출물")
    monkeypatch.setenv("DRIVE_PUBLISH_PERIOD", "2026-07")
    calls: list[list[str]] = []
    responses = iter(
        [
            {"files": []},
            {"id": "root-id"},
            {"files": []},
            {"id": "type-id"},
            {"files": []},
            {"id": "period-id"},
            {"id": "file-1"},
            {"webViewLink": "https://drive.google.com/file/d/file-1/view"},
        ]
    )

    def fake_run_json(argv: list[str]) -> dict[str, object]:
        calls.append(argv)
        return next(responses)

    monkeypatch.setattr(dp, "_run_json", fake_run_json)
    target = tmp_path / "report-20260716.md"
    target.write_text("x", encoding="utf-8")

    link = dp.publish(target, "report")

    assert link == "https://drive.google.com/file/d/file-1/view"
    creates = [json.loads(c[c.index("--json") + 1]) for c in calls if c[2:4] == ["files", "create"]]
    assert [c["name"] for c in creates] == ["Autophagy 산출물", "report", "2026-07"]
    upload = next(c for c in calls if "+upload" in c)
    assert upload[upload.index("--parent") + 1] == "period-id"


def test_best_effort_opt_in_off_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)

    def explode(argv: list[str]) -> dict[str, object]:
        raise AssertionError("must not touch gws unless DRIVE_PUBLISH_ENABLED=1")

    monkeypatch.setattr(dp, "_run_json", explode)
    assert dp.publish_best_effort(tmp_path / "x.md", "report") == ""


def test_best_effort_enabled_swallows_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")

    def boom(argv: list[str]) -> dict[str, object]:
        raise dp.DrivePublishError("gws down")

    monkeypatch.setattr(dp, "_run_json", boom)
    assert dp.publish_best_effort(tmp_path / "x.md", "report") == ""
