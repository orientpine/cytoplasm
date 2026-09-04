"""Idempotent gws Drive folder/file upsert specs via an in-memory fake gws (E11 S8)."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
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
        self.trashed_ids: set[str] = set()
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
            if params.get("fields") == "id,trashed,parents":
                return {
                    "id": params["fileId"],
                    "trashed": params["fileId"] in self.trashed_ids,
                    "parents": ["root"],
                }
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


def test_ensure_folder_path_replaces_a_trashed_cached_folder(tmp_path: Path) -> None:
    fake = FakeGws()
    fake.trashed_ids.add("old-root")
    cache = tmp_path / "folders.json"
    cache.write_text(json.dumps({"Root": "old-root", "Root/plans": "old-plans"}), encoding="utf-8")
    fake.folders[("Root", "root")] = "new-root"

    assert _client(tmp_path, fake).ensure_folder_path(("Root", "plans")) == "fold1"

    assert len(_calls_of(fake, "files", "list")) == 2
    assert json.loads(cache.read_text(encoding="utf-8")) == {
        "Root": "new-root",
        "Root/plans": "fold1",
    }


def test_ensure_folder_path_keeps_an_alive_cached_folder(tmp_path: Path) -> None:
    fake = FakeGws()
    cache = tmp_path / "folders.json"
    cache.write_text(json.dumps({"Root": "live-root"}), encoding="utf-8")

    assert _client(tmp_path, fake).ensure_folder_path(("Root",)) == "live-root"

    assert len(_calls_of(fake, "files", "get")) == 1
    assert not _calls_of(fake, "files", "list")
    assert not _calls_of(fake, "files", "create")


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


def test_live_upload_runs_from_source_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "artifact.bin"
    local.write_bytes(b"payload")
    observed: dict[str, object] = {}

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(argv=argv, cwd=kwargs.get("cwd"))
        return subprocess.CompletedProcess(argv, 0, '{"id":"f-1"}', "")

    monkeypatch.setattr(subprocess, "run", run)
    client = DriveClient("gws", tmp_path / "folders.json")

    assert client._upload_new(local, "artifact.bin", "parent-1") == "f-1"
    argv = observed["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("+upload") + 1] == local.name
    assert observed["cwd"] == local.parent


def test_live_upload_resolves_relative_executable_before_changing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = tmp_path / "caller"
    caller.mkdir()
    local = tmp_path / "artifacts" / "artifact.bin"
    local.parent.mkdir()
    local.write_bytes(b"payload")
    observed: dict[str, object] = {}

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(argv=argv, cwd=kwargs.get("cwd"))
        return subprocess.CompletedProcess(argv, 0, '{"id":"f-1"}', "")

    monkeypatch.chdir(caller)
    monkeypatch.setattr(subprocess, "run", run)
    client = DriveClient("./relative/gws", tmp_path / "folders.json")

    assert client._upload_new(local, "artifact.bin", "parent-1") == "f-1"
    argv = observed["argv"]
    assert isinstance(argv, list)
    assert Path(argv[0]).is_absolute()
    assert Path(argv[0]) == caller / "relative" / "gws"
    assert observed["cwd"] == local.parent


def test_live_update_runs_from_source_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "artifact.bin"
    local.write_bytes(b"payload")
    observed: dict[str, object] = {}

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(argv=argv, cwd=kwargs.get("cwd"))
        return subprocess.CompletedProcess(argv, 0, '{"id":"f-1"}', "")

    monkeypatch.setattr(subprocess, "run", run)
    client = DriveClient("gws", tmp_path / "folders.json")

    assert client._update_media("f-1", local) == "f-1"
    argv = observed["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("--upload") + 1] == local.name
    assert observed["cwd"] == local.parent


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


def test_live_download_captures_gws_media_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"live gws payload\n"
    local = tmp_path / "a.md"
    local.write_bytes(payload)
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        cwd = Path(str(kwargs["cwd"]))
        (cwd / "download.bin").write_bytes(payload)
        stdout = json.dumps(
            {
                "bytes": len(payload),
                "mimeType": "text/plain",
                "saved_file": "download.bin",
                "status": "success",
            }
        ).encode()
        return subprocess.CompletedProcess(argv, 0, stdout, b"")

    monkeypatch.setattr(subprocess, "run", run)
    client = DriveClient("gws", tmp_path / "folders.json")

    digest = client.download_and_verify("f-1", local)

    assert digest == hashlib.sha256(payload).hexdigest()
    assert json.loads(calls[0][calls[0].index("--params") + 1]) == {
        "fileId": "f-1",
        "alt": "media",
    }
    assert "-o" not in calls[0]


def test_download_and_verify_raises_on_sha256_mismatch(tmp_path: Path) -> None:
    local = tmp_path / "a.md"
    local.write_bytes(b"local bytes")
    fake = FakeGws()
    fake.remote_bytes = b"tampered bytes"

    with pytest.raises(DriveClientError):
        _client(tmp_path, fake).download_and_verify("f-1", local)


def test_move_file_records_exact_update_argv(tmp_path: Path) -> None:
    fake = FakeGws()
    client = _client(tmp_path, fake)
    assert client.move_file("file-1", "parent-new", "parent-old") == "file-1"
    call = fake.calls[-1]
    assert call[:4] == ["gws", "drive", "files", "update"]
    assert json.loads(call[call.index("--params") + 1]) == {
        "fileId": "file-1", "addParents": "parent-new", "removeParents": "parent-old"
    }


def test_rename_file_records_name_json(tmp_path: Path) -> None:
    fake = FakeGws()
    client = _client(tmp_path, fake)
    assert client.rename_file("file-1", "renamed.md") == "file-1"
    call = fake.calls[-1]
    assert call[:4] == ["gws", "drive", "files", "update"]
    assert json.loads(call[call.index("--params") + 1]) == {"fileId": "file-1"}
    assert json.loads(call[call.index("--json") + 1]) == {"name": "renamed.md"}


def test_list_children_paginates_and_combines(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    responses = iter([
        {"files": [{"id": "a", "name": "A"}], "nextPageToken": "next"},
        {"files": [{"id": "b", "name": "B"}]},
    ])
    def runner(argv: list[str]) -> dict[str, object]:
        calls.append(argv)
        return next(responses)
    result = _client(tmp_path, runner)
    assert result.list_children("folder-1") == [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    assert len(calls) == 2
    first = json.loads(calls[0][calls[0].index("--params") + 1])
    second = json.loads(calls[1][calls[1].index("--params") + 1])
    assert first == {"q": "'folder-1' in parents and trashed = false", "fields": "files(id,name,mimeType,modifiedTime,createdTime,size)", "pageSize": 1000}
    assert second["pageToken"] == "next"


def test_trash_file_and_new_methods_wrap_runner_failure(tmp_path: Path) -> None:
    def boom(argv: list[str]) -> dict[str, object]:
        raise RuntimeError("boom")
    client = _client(tmp_path, boom)
    for method, args in [(client.move_file, ("f", "a", "b")), (client.rename_file, ("f", "n")), (client.list_children, ("f",)), (client.trash_file, ("f",))]:
        with pytest.raises(DriveClientError):
            method(*args)

    fake = FakeGws()
    client = _client(tmp_path, fake)
    assert client.trash_file("file-1") == "file-1"
    call = fake.calls[-1]
    assert json.loads(call[call.index("--params") + 1]) == {"fileId": "file-1"}
    assert json.loads(call[call.index("--json") + 1]) == {"trashed": True}


def test_download_and_verify_is_fail_closed_when_nothing_downloaded(tmp_path: Path) -> None:
    local = tmp_path / "a.md"
    local.write_bytes(b"local bytes")

    def silent(argv: list[str]) -> dict[str, object]:  # noqa: ARG001 - writes no output file
        return {}

    client = DriveClient(gws_bin="gws", folder_cache=tmp_path / "folders.json", runner=silent)
    with pytest.raises(DriveClientError):
        client.download_and_verify("f-1", local)


def test_download_file_writes_remote_bytes_and_returns_sha256(tmp_path: Path) -> None:
    payload = "회의 녹음 바이트\n".encode()
    fake = FakeGws()
    fake.remote_bytes = payload
    dest = tmp_path / "downloaded.m4a"

    digest = _client(tmp_path, fake).download_file("f-audio", dest)

    assert dest.read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    fetched = _calls_of(fake, "files", "get")
    assert json.loads(fetched[0][fetched[0].index("--params") + 1]) == {
        "fileId": "f-audio",
        "alt": "media",
    }


def test_download_file_is_fail_closed_when_nothing_downloaded(tmp_path: Path) -> None:
    def silent(argv: list[str]) -> dict[str, object]:
        return {}

    client = DriveClient(gws_bin="gws", folder_cache=tmp_path / "folders.json", runner=silent)
    with pytest.raises(DriveClientError):
        client.download_file("f-audio", tmp_path / "out.m4a")
    assert not (tmp_path / "out.m4a").exists()
