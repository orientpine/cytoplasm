"""Idempotent gws Drive folder/file upsert specs via an in-memory fake gws (E11 S8)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from automation.drive_client import DriveClientError, DriveClient


def _parse_q(query: str) -> tuple[str, str, bool]:
    name = re.search(r"name = '((?:[^'\\]|\\.)*)'", query).group(1)
    name = name.replace("\\'", "'").replace("\\\\", "\\")
    parent = re.search(r"'([^']+)' in parents", query).group(1)
    return name, parent, "application/vnd.google-apps.folder" in query


class FakeGws:
    def __init__(self) -> None:
        self.folders: dict[tuple[str, str], str] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.calls: list[list[str]] = []
        self.permissions: list[dict[str, object]] = [{"id": "p-own", "type": "user", "role": "owner"}]
        self.remote_bytes = b""
        self._n = 0

    def _new(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def __call__(self, argv: list[str]) -> dict[str, object]:
        self.calls.append(argv)
        op = argv[2]
        if op == "+upload":
            parent = argv[argv.index("--parent") + 1]
            name = argv[argv.index("--name") + 1]
            file_id = self._new("file")
            self.files[(name, parent)] = file_id
            return {"id": file_id}
        method = argv[3]
        if op == "permissions":
            if method != "list":
                raise AssertionError(f"unexpected argv {argv}")
            return {"permissions": list(self.permissions)}
        if method == "list":
            params = json.loads(argv[argv.index("--params") + 1])
            name, parent, is_folder = _parse_q(params["q"])
            registry = self.folders if is_folder else self.files
            found = registry.get((name, parent))
            return {"files": [{"id": found, "name": name}] if found else []}
        if method == "create":
            meta = json.loads(argv[argv.index("--json") + 1])
            folder_id = self._new("fold")
            self.folders[(meta["name"], meta["parents"][0])] = folder_id
            return {"id": folder_id}
        if method == "update":
            params = json.loads(argv[argv.index("--params") + 1])
            return {"id": params["fileId"]}
        if method == "get":
            params = json.loads(argv[argv.index("--params") + 1])
            if params.get("alt") == "media":
                Path(argv[argv.index("-o") + 1]).write_bytes(self.remote_bytes)
                return {}
            return {"webViewLink": f"https://drive.google.com/file/d/{params['fileId']}/view"}
        raise AssertionError(f"unexpected argv {argv}")


def _creates(fake: FakeGws) -> int:
    return sum(1 for call in fake.calls if call[2:4] == ["files", "create"])


def _calls_of(fake: FakeGws, op: str, method: str) -> list[list[str]]:
    return [call for call in fake.calls if call[2:4] == [op, method]]


def _client(tmp_path: Path, fake: FakeGws) -> DriveClient:
    return DriveClient(gws_bin="gws", folder_cache=tmp_path / "folders.json", runner=fake)


def test_ensure_folder_path_is_idempotent(tmp_path: Path) -> None:
    fake = FakeGws()
    client = DriveClient(gws_bin="gws", folder_cache=tmp_path / "folders.json", runner=fake)

    first = client.ensure_folder_path(("Root", "plans"))
    creates_after_first = _creates(fake)
    assert creates_after_first == 2  # Root + plans

    second = client.ensure_folder_path(("Root", "plans"))
    assert first == second
    assert _creates(fake) == creates_after_first  # cache hit → no new folder created


def test_upsert_file_creates_then_updates(tmp_path: Path) -> None:
    fake = FakeGws()
    client = DriveClient(gws_bin="gws", folder_cache=tmp_path / "folders.json", runner=fake)
    parent = client.ensure_folder_path(("Root", "plans"))
    local = tmp_path / "a.md"
    local.write_text("x", encoding="utf-8")

    created = client.upsert_file(local, "a.md", parent)
    assert created["action"] == "created"
    assert created["id"]
    assert created["webViewLink"].startswith("https://")

    updated = client.upsert_file(local, "a.md", parent)
    assert updated["action"] == "updated"
    assert updated["id"] == created["id"]


def test_runner_failure_is_fail_closed(tmp_path: Path) -> None:
    def boom(argv: list[str]) -> dict[str, object]:
        raise DriveClientError("rc=1")

    client = DriveClient(gws_bin="gws", folder_cache=tmp_path / "folders.json", runner=boom)
    with pytest.raises(DriveClientError):
        client.ensure_folder_path(("Root",))


def test_verify_owner_only_passes_when_owner_is_sole_principal(tmp_path: Path) -> None:
    fake = FakeGws()
    fake.permissions = [{"id": "p-own", "type": "user", "role": "owner"}]

    _client(tmp_path, fake).verify_owner_only("f-1")

    listed = _calls_of(fake, "permissions", "list")
    assert len(listed) == 1
    assert listed[0][:4] == ["gws", "drive", "permissions", "list"]
    assert json.loads(listed[0][listed[0].index("--params") + 1])["fileId"] == "f-1"


@pytest.mark.parametrize(
    ("extra", "token"),
    [
        ({"id": "p-any", "type": "anyone", "role": "reader"}, "type=anyone"),
        ({"id": "p-dom", "type": "domain", "role": "reader", "domain": "x.ac.kr"}, "type=domain"),
        ({"id": "p-w", "type": "user", "role": "writer"}, "role=writer"),
        ({"id": "p-o", "type": "user", "role": "organizer"}, "role=organizer"),
        ({"id": "p-f", "type": "user", "role": "fileOrganizer"}, "role=fileOrganizer"),
        ({"id": "p-r", "type": "user", "role": "reader"}, "role=reader"),
    ],
    ids=["anyone", "domain", "writer", "organizer", "fileOrganizer", "extra-reader"],
)
def test_verify_owner_only_rejects_shared_file(
    tmp_path: Path, extra: dict[str, str], token: str
) -> None:
    fake = FakeGws()
    fake.permissions = [{"id": "p-own", "type": "user", "role": "owner"}, extra]

    # The offending principal's own type/role token must reach the operator, not a generic count.
    with pytest.raises(DriveClientError, match=re.escape(token)):
        _client(tmp_path, fake).verify_owner_only("f-1")


def test_verify_owner_only_is_fail_closed_on_empty_permissions(tmp_path: Path) -> None:
    fake = FakeGws()
    fake.permissions = []

    with pytest.raises(DriveClientError):
        _client(tmp_path, fake).verify_owner_only("f-1")


def test_download_and_verify_returns_sha256_when_bytes_match(tmp_path: Path) -> None:
    payload = b"drive-archive payload\n"
    local = tmp_path / "a.md"
    local.write_bytes(payload)
    fake = FakeGws()
    fake.remote_bytes = payload

    digest = _client(tmp_path, fake).download_and_verify("f-1", local)

    assert digest == hashlib.sha256(payload).hexdigest()
    fetched = _calls_of(fake, "files", "get")
    assert len(fetched) == 1
    params = json.loads(fetched[0][fetched[0].index("--params") + 1])
    assert params == {"fileId": "f-1", "alt": "media"}
    assert "-o" in fetched[0]


def test_download_and_verify_raises_on_sha256_mismatch(tmp_path: Path) -> None:
    local = tmp_path / "a.md"
    local.write_bytes(b"local bytes")
    fake = FakeGws()
    fake.remote_bytes = b"tampered bytes"

    with pytest.raises(DriveClientError):
        _client(tmp_path, fake).download_and_verify("f-1", local)


def test_download_and_verify_is_fail_closed_when_nothing_downloaded(tmp_path: Path) -> None:
    local = tmp_path / "a.md"
    local.write_bytes(b"local bytes")

    def silent(argv: list[str]) -> dict[str, object]:  # noqa: ARG001 - writes no output file
        return {}

    client = DriveClient(gws_bin="gws", folder_cache=tmp_path / "folders.json", runner=silent)
    with pytest.raises(DriveClientError):
        client.download_and_verify("f-1", local)
