"""State-file Drive facade specs using the shared in-memory gws fake."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from automation.drive_client import DriveClient
from automation.drive_outputs import fetch_state_file, publish_state_file
from automation.drive_taxonomy import TaxonomyError, category_parts
from tests.unit.test_drive_outputs import FakeGws


def _client(tmp_path: Path, fake: FakeGws) -> DriveClient:
    return DriveClient("fake-gws", tmp_path / "folders.json", runner=fake)


def test_category_parts_returns_its_non_dated_category_folder() -> None:
    assert category_parts("meeting") == ("autophagy", "회의록")


def test_category_parts_refuses_gate_only_kind() -> None:
    with pytest.raises(TaxonomyError):
        _ = category_parts("patent")


def test_publish_state_file_uses_exact_parts_and_verifies_readback(tmp_path: Path) -> None:
    fake = FakeGws()
    client = _client(tmp_path, fake)
    local = tmp_path / "action-items.csv"
    _ = local.write_bytes(b"owner,action\nKim,review\n")
    parts = (*category_parts("meeting"), "프로젝트A")

    link = publish_state_file(parts, "action-items.csv", local, client=client)

    assert link == "https://drive.google.test/file-4"
    assert [call[call.index("--name") + 1] for call in fake.calls if call[2] == "+upload"] == [
        "action-items.csv"
    ]
    assert [
        item["name"]
        for items in fake.children.values()
        for item in items
        if item["mimeType"] == "application/vnd.google-apps.folder"
    ] == ["autophagy", "회의록", "프로젝트A"]
    assert not any(item["name"] == "2026" for items in fake.children.values() for item in items)
    assert any(call[2:4] == ["permissions", "list"] for call in fake.calls)
    assert any(
        call[2:4] == ["files", "get"] and '"alt": "media"' in call[call.index("--params") + 1]
        for call in fake.calls
    )


def test_publish_state_file_refuses_path_like_name(tmp_path: Path) -> None:
    local = tmp_path / "state.csv"
    _ = local.write_bytes(b"state")

    with pytest.raises(TaxonomyError):
        _ = publish_state_file(
            ("autophagy", "회의록"),
            "bad/name.csv",
            local,
            client=_client(tmp_path, FakeGws()),
        )


def test_fetch_state_file_returns_false_when_the_child_is_missing(tmp_path: Path) -> None:
    found = fetch_state_file(
        ("autophagy", "회의록"), "프로젝트-코드.csv", tmp_path / "download.csv",
        client=_client(tmp_path, FakeGws()),
    )

    assert found is False
    assert not (tmp_path / "download.csv").exists()


def test_fetch_state_file_downloads_the_nfc_matching_child(tmp_path: Path) -> None:
    fake = FakeGws()
    client = _client(tmp_path, fake)
    parts = ("autophagy", "회의록")
    parent = client.ensure_folder_path(parts)
    remote_name = unicodedata.normalize("NFD", "프로젝트-코드.csv")
    payload = "PRJ-1,프로젝트A\n".encode()
    _ = fake.seed_file(remote_name, parent, "codes", payload)
    dest = tmp_path / "download.csv"

    found = fetch_state_file(parts, "프로젝트-코드.csv", dest, client=client)

    assert found is True
    assert dest.read_bytes() == payload
