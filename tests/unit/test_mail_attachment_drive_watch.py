"""no-agent cron contract for the attachment archive wrapper.

Hermes delivers a ``--no-agent`` script's stdout verbatim and drops stderr, so a
successful tick must print nothing and a failure must be exactly one
``MAIL-ATTACHMENT-DRIVE-FAIL code=<code>`` line. The wrapper runs the sync CLI
from the governed live mount; when that mount is absent the tick is a reported
failure (``code=unmounted``), never a silent no-op — an unarchived attachment
looks exactly like an idle one otherwise.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PATH = _REPO / "skills" / "mail" / "scripts" / "mail_attachment_drive_watch.py"
_SPEC = importlib.util.spec_from_file_location("mail_attachment_drive_watch", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
watcher = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = watcher
_SPEC.loader.exec_module(watcher)

_SILENT_SUCCESS = "import sys\nsys.exit(0)\n"
_JSON_FAILURE = (
    "import json, sys\n"
    "print(json.dumps({'status': 'error', 'code': 'checksum_mismatch'}), file=sys.stderr)\n"
    "sys.exit(1)\n"
)
_NO_PAYLOAD_FAILURE = "import sys\nsys.stderr.write('traceback noise\\n')\nsys.exit(1)\n"


def _stub_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    stub = tmp_path / "stub_sync.py"
    stub.write_text(body, encoding="utf-8")
    monkeypatch.setenv(watcher.SYNC_ENV, str(stub))
    return stub


def test_default_sync_cli_is_the_governed_live_mount() -> None:
    assert watcher.SYNC == Path(
        "/srv/autophagy-skills/live/mail/scripts/mail_attachment_drive_sync.py"
    )


def test_successful_tick_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_sync(tmp_path, monkeypatch, _SILENT_SUCCESS)

    assert watcher.main() == 0
    assert capsys.readouterr().out == ""


def test_child_failure_prints_one_marker_with_the_child_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_sync(tmp_path, monkeypatch, _JSON_FAILURE)

    assert watcher.main() == 1
    assert capsys.readouterr().out == "MAIL-ATTACHMENT-DRIVE-FAIL code=checksum_mismatch\n"


def test_child_failure_without_a_payload_still_reports_one_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_sync(tmp_path, monkeypatch, _NO_PAYLOAD_FAILURE)

    assert watcher.main() == 1
    assert capsys.readouterr().out == "MAIL-ATTACHMENT-DRIVE-FAIL code=unknown\n"


def test_absent_sync_cli_reports_unmounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(watcher.SYNC_ENV, str(tmp_path / "not-mounted.py"))

    assert watcher.main() == 1
    assert capsys.readouterr().out == "MAIL-ATTACHMENT-DRIVE-FAIL code=unmounted\n"


def test_child_runs_with_the_wrapper_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # cron hands the wrapper no secrets: whatever it loaded must reach the child.
    seen = tmp_path / "seen.txt"
    _stub_sync(
        tmp_path,
        monkeypatch,
        "import os, pathlib, sys\n"
        f"pathlib.Path({str(seen)!r}).write_text(os.environ.get('MAILON_TEST_SECRET', ''))\n"
        "sys.exit(0)\n",
    )
    monkeypatch.setenv("MAILON_TEST_SECRET", "token-42")

    assert watcher.main() == 0
    assert seen.read_text(encoding="utf-8") == "token-42"
    assert capsys.readouterr().out == ""


def test_env_secrets_are_loaded_into_this_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = tmp_path / ".env.secrets"
    secrets.write_text(
        '# comment\nMAILON_TEST_LOADED="token-7"\nMAILON_TEST_KEPT=fresh\n', encoding="utf-8"
    )
    monkeypatch.delenv("MAILON_TEST_LOADED", raising=False)
    monkeypatch.setenv("MAILON_TEST_KEPT", "already-set")

    watcher._load_env_secrets(secrets)

    try:
        assert os.environ["MAILON_TEST_LOADED"] == "token-7"
        assert os.environ["MAILON_TEST_KEPT"] == "already-set"  # never overwritten
    finally:
        os.environ.pop("MAILON_TEST_LOADED", None)


def test_missing_env_secrets_file_is_not_an_error(tmp_path: Path) -> None:
    watcher._load_env_secrets(tmp_path / "absent")
