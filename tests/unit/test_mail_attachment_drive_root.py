"""Mail attachment archive follows the single Drive outputs root (DRIVE_OUTPUTS_ROOT).

Kept apart from ``test_mail_attachment_drive_sync.py`` so that file's existing cases stay
byte-identical. The archive used to spell its root as a constant, so renaming the Drive
outputs root moved every other skill while the next attachment tick looked the old name up
and created a fresh root folder beside the renamed one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "mail" / "scripts"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_SCRIPTS))

from automation.drive_client import DriveClient  # noqa: E402
import mail_attachment_archive as archive  # noqa: E402

_PATH = _SCRIPTS / "mail_attachment_drive_sync.py"
_SPEC = importlib.util.spec_from_file_location("mail_attachment_drive_sync_root", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
syncer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = syncer
_SPEC.loader.exec_module(syncer)

_SYNC_TESTS = importlib.util.spec_from_file_location(
    "test_mail_attachment_drive_sync_fakes", Path(__file__).with_name("test_mail_attachment_drive_sync.py")
)
assert _SYNC_TESTS is not None and _SYNC_TESTS.loader is not None
_fakes = importlib.util.module_from_spec(_SYNC_TESTS)
_SYNC_TESTS.loader.exec_module(_fakes)
FakeGws = _fakes.FakeGws

RENAMED = "Dori's Home"


def _item(local: Path) -> archive.Attachment:
    stat = local.stat()
    return archive.Attachment(
        key=archive.attachment_key("u1", local.name), uid="u1", filename=local.name,
        local=local, size=stat.st_size, mtime_ns=stat.st_mtime_ns,
        year="2026", month="10", mailbox="inbox",
    )


def test_folder_parts_follow_the_outputs_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "a.pdf"
    local.write_bytes(b"a")
    item = _item(local)

    monkeypatch.delenv("DRIVE_OUTPUTS_ROOT", raising=False)
    assert archive.folder_parts(item) == ("autophagy", "메일 첨부파일", "2026", "10", "inbox")

    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", RENAMED)
    assert archive.folder_parts(item) == (RENAMED, "메일 첨부파일", "2026", "10", "inbox")


def test_renamed_root_uploads_into_the_same_folder_without_creating_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: Drive holds only the renamed root (same ids), the folder cache has no
    # entry to fall back on, and DRIVE_OUTPUTS_ROOT names the new root.
    state_dir = tmp_path / "archive"
    monkeypatch.setattr(archive, "STATE_DIR", state_dir)
    monkeypatch.setattr(archive, "STATE_DB", state_dir / "archive.db")
    fake = FakeGws()
    fake.folders.update({
        (RENAMED, "root"): "fold-root",
        ("메일 첨부파일", "fold-root"): "fold-mail",
        ("2026", "fold-mail"): "fold-2026",
        ("10", "fold-2026"): "fold-10",
        ("inbox", "fold-10"): "fold-inbox",
    })
    local = tmp_path / "report.pdf"
    local.write_bytes(b"report")
    item = _item(local)
    monkeypatch.setattr(archive, "discover", lambda: ([item], 0, 0))
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", RENAMED)
    client = DriveClient(gws_bin="gws", folder_cache=archive.folder_cache(), runner=fake)

    result = syncer.sync(client=client, workers=1)

    assert (result["uploaded"], result["failed"]) == (1, 0)
    created = [call for call in fake.calls if call[2:4] == ["files", "create"]]
    assert created == []  # no new "autophagy" and no second "Dori's Home"
    uploads = [call for call in fake.calls if call[2] == "+upload"]
    assert [call[call.index("--parent") + 1] for call in uploads] == ["fold-inbox"]
