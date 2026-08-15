"""Completeness guard for the E11 drive-archive retirement.

drive-archive mirrored git-tracked developer docs (.omo/plans, notepads,
docs/features·qa·patch) to Drive — pure redundancy with GitHub, so the owner
retired it. This guard is the RED→GREEN gate for the removal: it fails while any
drive-archive code path survives and passes once the subsystem is gone, WITHOUT
touching the separate ``drive_publish`` output-publishing system or the shared
``DriveClient`` the doctype skill still uses.

Scope is code paths only (automation/, skills/, configs/, tests/). Docs keep
legitimate historical mentions (past-incident citations, discord-arch history);
their freshness is enforced by the removal task list, not by this scanner.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SELF = Path(__file__).resolve()
_CODE_ROOTS = ("automation", "skills", "configs", "tests")
_FORBIDDEN = ("drive_archive", "DRIVE_ARCHIVE", "Flow.DRIVE")


def _tracked_code_files() -> list[Path]:
    found: list[Path] = []
    for root in _CODE_ROOTS:
        for path in (_REPO / root).rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.resolve() == _SELF:
                continue
            found.append(path)
    return found


def test_drive_archive_package_is_gone() -> None:
    assert not (_REPO / "automation" / "drive_archive").exists()


def test_drive_archive_test_files_are_gone() -> None:
    stray = sorted(
        p.relative_to(_REPO).as_posix()
        for p in (_REPO / "tests").rglob("*drive_archive*")
        if p.resolve() != _SELF
    )
    assert stray == []


def test_no_forbidden_drive_archive_tokens_in_code() -> None:
    offenders: list[str] = []
    for path in _tracked_code_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for token in _FORBIDDEN:
            if token in text:
                offenders.append(f"{path.relative_to(_REPO).as_posix()}: {token}")
    assert offenders == []


def test_approval_kind_has_no_drive_archive() -> None:
    from automation.interop.approval_surface import ApprovalKind

    assert not hasattr(ApprovalKind, "DRIVE_ARCHIVE")
    assert "drive-archive" not in {kind.value for kind in ApprovalKind}


def test_audit_flow_has_no_drive() -> None:
    from automation.interop.approval_surface_audit import Flow

    assert not hasattr(Flow, "DRIVE")


def test_denylist_has_no_drive_archive_rule() -> None:
    text = (_REPO / "configs" / "external-effect-tools.yaml").read_text(encoding="utf-8")
    assert "drive_archive_batch_upload" not in text


# --- survival: the systems that MUST keep working -------------------------


def test_shared_drive_client_survives_relocated() -> None:
    assert (_REPO / "automation" / "drive_client.py").exists()
    spec = importlib.util.find_spec("automation.drive_client")
    assert spec is not None


def test_drive_publish_is_untouched() -> None:
    hashes = {
        (_REPO / "skills" / skill / "scripts" / "drive_publish.py").read_bytes()
        for skill in ("doctype", "proposal", "report")
    }
    assert len(hashes) == 1  # all three byte-identical


def test_doctype_save_still_chains_the_drive_upload() -> None:
    text = (_REPO / "skills" / "doctype" / "scripts" / "doctype_save.py").read_text(
        encoding="utf-8"
    )
    assert "from automation.drive_client import" in text
    for call in ("ensure_folder_path", "upsert_file", "verify_owner_only", "download_and_verify"):
        assert call in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
