"""``DriveClient.file_checksum`` — the read-only verification seam (no media re-fetch).

The mail attachment archive verifies a 2.9 GB corpus after upload. Re-downloading
every object (``download_and_verify``) doubles the traffic on every tick, so the
verification compares Drive's own ``sha256Checksum``/``size`` metadata instead. A
response missing either field must fail closed: an unverified upload may never be
recorded as verified.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.drive_client import DriveClient, DriveClientError


def _client(tmp_path: Path, runner: object) -> DriveClient:
    return DriveClient(gws_bin="gws", folder_cache=tmp_path / "folders.json", runner=runner)


def test_file_checksum_returns_remote_sha256_and_size(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> dict[str, object]:
        calls.append(argv)
        return {"sha256Checksum": "AB" * 32, "size": "4096"}

    checksum, size = _client(tmp_path, runner).file_checksum("f-1")

    assert (checksum, size) == ("AB" * 32, 4096)
    assert calls[0][:4] == ["gws", "drive", "files", "get"]
    assert json.loads(calls[0][calls[0].index("--params") + 1]) == {
        "fileId": "f-1",
        "fields": "sha256Checksum,size",
    }
    # A metadata read only — the bytes stay on the far side.
    assert "alt" not in calls[0][calls[0].index("--params") + 1]
    assert len(calls) == 1


def test_file_checksum_accepts_an_integer_size(tmp_path: Path) -> None:
    def runner(argv: list[str]) -> dict[str, object]:
        return {"sha256Checksum": "cd" * 32, "size": 17}

    assert _client(tmp_path, runner).file_checksum("f-1") == ("cd" * 32, 17)


@pytest.mark.parametrize(
    "response",
    [
        {"size": "10"},
        {"sha256Checksum": "", "size": "10"},
        {"sha256Checksum": "ab" * 32},
        {},
        {"sha256Checksum": "ab" * 32, "size": "not-a-number"},
    ],
    ids=["no-checksum", "empty-checksum", "no-size", "empty", "size-not-integer"],
)
def test_file_checksum_is_fail_closed_on_an_unusable_response(
    tmp_path: Path, response: dict[str, object]
) -> None:
    def runner(argv: list[str]) -> dict[str, object]:
        return response

    with pytest.raises(DriveClientError):
        _client(tmp_path, runner).file_checksum("f-1")


def test_file_checksum_propagates_a_failed_call(tmp_path: Path) -> None:
    def boom(argv: list[str]) -> dict[str, object]:
        raise DriveClientError("rc=1")

    with pytest.raises(DriveClientError):
        _client(tmp_path, boom).file_checksum("f-1")
