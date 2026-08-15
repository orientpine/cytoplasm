"""procurement Drive upload targets an organized 4-level project folder (not root).

`Autophagy 산출물 / procurement / <YYYY-MM> / <file>` — the review-DM
size branch must never dump work products to the Drive root again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "procurement" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import procure_review as pr  # noqa: E402


def test_drive_upload_targets_dated_project_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROCURE_DRIVE_PERIOD", "2026-07")
    calls: list[list[str]] = []
    responses = iter(
        [
            {"files": []},  # list root name -> miss
            {"id": "fold-root"},  # create root
            {"files": []},  # list procurement -> miss
            {"id": "fold-proc"},  # create procurement
            {"files": []},  # list 2026-07 -> miss
            {"id": "fold-period"},  # create 2026-07
            {"id": "file-1"},  # +upload
            {"webViewLink": "https://drive.google.com/file/d/file-1/view"},  # files get
        ]
    )

    def fake_run_json(argv: list[str]) -> dict[str, object]:
        calls.append(argv)
        return next(responses)

    monkeypatch.setattr(pr, "_run_json", fake_run_json)
    target = tmp_path / "대형-용역요청서.hwpx"
    target.write_text("x", encoding="utf-8")

    link = pr._drive_upload(target)

    assert link == "https://drive.google.com/file/d/file-1/view"
    # folders created in order: category > subcategory > period
    creates = [json.loads(c[c.index("--json") + 1]) for c in calls if c[2:4] == ["files", "create"]]
    assert [c["name"] for c in creates] == ["Autophagy 산출물", "procurement", "2026-07"]
    # the upload is parented into the period folder (NOT Drive root)
    upload = next(c for c in calls if "+upload" in c)
    assert "--parent" in upload
    assert upload[upload.index("--parent") + 1] == "fold-period"


def test_drive_upload_reuses_existing_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROCURE_DRIVE_PERIOD", "2026-07")
    responses = iter(
        [
            {"files": [{"id": "fold-root"}]},  # list root name -> hit (no create)
            {"files": [{"id": "fold-proc"}]},  # list procurement -> hit
            {"files": [{"id": "fold-period"}]},  # list 2026-07 -> hit
            {"id": "file-9"},  # +upload
            {"webViewLink": "https://drive.google.com/file/d/file-9/view"},
        ]
    )
    creates: list[list[str]] = []

    def fake_run_json(argv: list[str]) -> dict[str, object]:
        if argv[2:4] == ["files", "create"]:
            creates.append(argv)
        return next(responses)

    monkeypatch.setattr(pr, "_run_json", fake_run_json)
    target = tmp_path / "big.hwpx"
    target.write_text("x", encoding="utf-8")

    link = pr._drive_upload(target)

    assert link.endswith("/file-9/view")
    assert creates == []  # idempotent: existing folders reused, none re-created
