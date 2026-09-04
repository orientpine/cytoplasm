"""PLAUD_SYNC_TIMEZONE picks the note zone; an unknown name falls back to Asia/Seoul out loud.

2026-09-04 (B안): the note's created/modified and its 한눈에 line are local time. The
node runs in UTC, so the zone is configuration — and a typo must not silently move
every note by nine hours, nor kill the tick.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_WATCH: Final = _REPO / "automation" / "plaud_sync" / "cron" / "plaud_sync_watch.py"


def _load_watch(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    spec = importlib.util.spec_from_file_location("plaud_sync_watch_for_tz_test", _WATCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_note_timezone_honours_the_env_and_falls_back_loudly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    watch = _load_watch(monkeypatch)

    monkeypatch.setenv("PLAUD_SYNC_TIMEZONE", "Europe/Berlin")
    assert watch._note_timezone().key == "Europe/Berlin"

    monkeypatch.setenv("PLAUD_SYNC_TIMEZONE", "Mars/Olympus_Mons")
    assert watch._note_timezone().key == "Asia/Seoul"
    assert "PLAUD_SYNC_TIMEZONE" in capsys.readouterr().err

    monkeypatch.delenv("PLAUD_SYNC_TIMEZONE")
    assert watch._note_timezone().key == "Asia/Seoul"
