"""gws re-serializes a JSON file's media, so a byte read-back alone cannot verify it.

Separate from ``test_drive_client.py`` so its FakeGws stays the plain byte-echo double;
this double answers ``alt=media`` the way live gws does for ``application/json``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from automation.drive_client import DriveClient, DriveClientError

_LOCAL = '{"title": "제목", "band_index": 0, "rows": [[1, 2]]}\n'.encode()


class JsonRenderingGws:
    def __init__(self, stored: bytes, *, rendered: bytes | None = None) -> None:
        self.stored = stored
        self.rendered = rendered if rendered is not None else self._gws_render(stored)
        self.checksum = hashlib.sha256(stored).hexdigest()
        self.size = len(stored)

    @staticmethod
    def _gws_render(stored: bytes) -> bytes:
        value = json.loads(stored)
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()

    def __call__(self, argv: list[str]) -> dict[str, object]:
        params = json.loads(argv[argv.index("--params") + 1])
        if params.get("alt") == "media":
            Path(argv[argv.index("-o") + 1]).write_bytes(self.rendered)
            return {}
        if params.get("fields") == "sha256Checksum,size":
            return {"sha256Checksum": self.checksum, "size": str(self.size)}
        raise AssertionError(f"unexpected argv {argv}")


def _client(tmp_path: Path, fake: JsonRenderingGws) -> DriveClient:
    return DriveClient("gws", tmp_path / "folders.json", runner=lambda argv, **_: fake(argv))


def _local(tmp_path: Path) -> Path:
    path = tmp_path / "figures.json"
    _ = path.write_bytes(_LOCAL)
    return path


def test_json_stored_byte_for_byte_verifies_through_drive_checksum(tmp_path: Path) -> None:
    fake = JsonRenderingGws(_LOCAL)
    assert fake.rendered != _LOCAL

    digest = _client(tmp_path, fake).download_and_verify("f-1", _local(tmp_path))

    assert digest == hashlib.sha256(_LOCAL).hexdigest()


def test_json_with_a_different_value_is_still_refused(tmp_path: Path) -> None:
    tampered = _LOCAL.replace("제목".encode(), "변조".encode())
    fake = JsonRenderingGws(_LOCAL, rendered=JsonRenderingGws._gws_render(tampered))  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(DriveClientError, match="불일치"):
        _ = _client(tmp_path, fake).download_and_verify("f-1", _local(tmp_path))


def test_same_json_value_with_foreign_stored_bytes_is_refused(tmp_path: Path) -> None:
    fake = JsonRenderingGws(_LOCAL)
    fake.checksum = hashlib.sha256(b'{"other": true}').hexdigest()

    with pytest.raises(DriveClientError, match="불일치"):
        _ = _client(tmp_path, fake).download_and_verify("f-1", _local(tmp_path))


def test_non_json_mismatch_does_not_consult_the_checksum(tmp_path: Path) -> None:
    local = tmp_path / "a.md"
    _ = local.write_bytes(b"local bytes")
    fake = JsonRenderingGws(b"{}", rendered=b"tampered bytes")
    fake.checksum = hashlib.sha256(b"local bytes").hexdigest()

    with pytest.raises(DriveClientError, match="불일치"):
        _ = _client(tmp_path, fake).download_and_verify("f-1", local)
