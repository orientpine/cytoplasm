"""Regression coverage for retirement of the vendored Drive publisher module."""

from __future__ import annotations

from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]


def test_legacy_drive_publish_module_is_not_importable_from_report_scripts() -> None:
    assert not (_REPO / "skills" / "report" / "scripts" / "drive_publish.py").exists()
