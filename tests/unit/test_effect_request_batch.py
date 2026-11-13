"""Several files on one Drive save card, one approval (issue #241).

Before this, every file was its own drive-save card and the owner pressed ✅ once per file.
Owner decisions: Q548 "아무 폴더 id나 허용" (any Drive folder id; the card shows its name and
path, the hash binds the id) and Q549 "나머지는 올리고 파일별 결과를 남겨 실패한 것만 다시 시도".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from automation import drive_outputs
from automation.drive_client import DriveClient, DriveClientError
from automation.effect_request import cli
from automation.effect_request.model import EffectRequestError, EffectRequestStore
from automation.effect_request.render import RENDER_VERSION_BATCH, render_effect_approval
from automation.effect_request.watch import notice_text
from tests.unit.test_effect_request import _approve, _post, _tick

FOLDER = "1AbCdEfGhIjKlMnOp"


def _args(files: list[Path], **extra: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "effect": "drive-save", "summary": "메일 첨부 저장", "file": [str(f) for f in files], "title": None,
        "kind": "doctype", "project": "", "folder": "", "on": "2026-11-13",
        "origin_channel_id": "", "origin_message_id": "",
    }
    values.update(extra)
    return argparse.Namespace(**values)


def _files(tmp_path: Path, *names: str) -> list[Path]:
    paths = []
    for index, name in enumerate(names):
        path = tmp_path / "in" / str(index) / name
        path.parent.mkdir(parents=True)
        path.write_bytes(f"{name} {index}\n".encode())
        paths.append(path)
    return paths


class Saved:
    def __init__(self, link: str) -> None:
        self.links = (link,)
        self.action = "created"


def test_one_card_lists_every_file_and_the_hash_binds_the_whole_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path, "공고문.pdf", "신청서.hwp")
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args(files), store)
    card = render_effect_approval(record)
    assert record.payload["version"] == 2 and len(record.payload["files"]) == 2
    for item in record.payload["files"]:
        assert f"{item['drive_name']} · " in card and f"sha256 {item['sha256'][:12]}" in card
    assert "파일 2개" in card and "Drive 위치: autophagy/문서/2026" in card and record.action_hash in card
    transport, posted = _post(monkeypatch, store, record)
    assert len(transport.messages) == 1 and posted.render_version == RENDER_VERSION_BATCH
    # A changed list (one more file, or other bytes) is another hash: it needs its own approval.
    more = cli._new_record(_args([*files, *_files(tmp_path / "b", "별첨.pdf")]), store)
    files[1].write_bytes(b"other bytes\n")
    changed = cli._new_record(_args(files), store)
    assert len({record.action_hash, more.action_hash, changed.action_hash}) == 3


def test_one_file_without_a_folder_keeps_the_v1_payload_and_card(tmp_path: Path) -> None:
    (only,) = _files(tmp_path, "조회결과.csv")
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args([only]), store)
    assert "version" not in record.payload and record.payload["drive_name"] == "2026-11-13_조회결과.csv"
    assert "판본: effect-request-v1" in render_effect_approval(record)


def test_titles_go_with_files_in_order_and_duplicates_or_mismatched_counts_are_refused(tmp_path: Path) -> None:
    files = _files(tmp_path, "mail-1-1.pdf", "mail-1-2.pdf")
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args(files, title=["공고문", "신청서"]), store)
    assert [item["drive_name"] for item in record.payload["files"]] == [
        "2026-11-13_공고문.pdf", "2026-11-13_신청서.pdf"]
    with pytest.raises(EffectRequestError, match="--title"):
        cli._new_record(_args(files, title=["하나"]), store)
    with pytest.raises(EffectRequestError, match="같은 Drive 이름"):
        cli._new_record(_args(files, title=["같음", "같음"]), store)


def test_a_card_too_long_to_post_is_refused_before_anything_is_stored(tmp_path: Path) -> None:
    files = _files(tmp_path, *(f"{'아주긴파일이름' * 8}{n}.pdf" for n in range(20)))
    store = EffectRequestStore(tmp_path / "state")
    with pytest.raises(EffectRequestError, match="카드 한 장"):
        cli._new_record(_args(files), store)
    assert store.all() == () and not any((store.root / "frozen").iterdir())
    with pytest.raises(EffectRequestError, match="1~20"):
        cli._new_record(_args([*files, files[0]]), store)


def test_a_folder_id_card_names_the_folder_and_the_hash_binds_the_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    looked: list[str] = []

    def target(folder_id: str) -> drive_outputs.FolderTarget:
        looked.append(folder_id)
        return drive_outputs.FolderTarget(folder_id, "받은 첨부", ("내 드라이브", "연구", "받은 첨부"))

    monkeypatch.setattr(drive_outputs, "folder_target", target)
    files = _files(tmp_path, "공고문.pdf", "신청서.hwp")
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args(files, folder=FOLDER), store)
    card = render_effect_approval(record)
    assert looked == [FOLDER]
    assert f"저장 폴더: 받은 첨부 (경로 내 드라이브/연구/받은 첨부, id {FOLDER})" in card
    assert [item["drive_name"] for item in record.payload["files"]] == ["공고문.pdf", "신청서.hwp"]
    other = cli._new_record(_args(files, folder=FOLDER + "x"), store)
    assert other.action_hash != record.action_hash
    with pytest.raises(EffectRequestError, match="--project"):
        cli._new_record(_args(files, folder=FOLDER, project="과제"), store)


def _drive(rows: dict[str, object]) -> DriveClient:
    def runner(argv: list[str]) -> object:
        params = json.loads(argv[argv.index("--params") + 1])
        key = ("perm:" if argv[2] == "permissions" else "") + params["fileId"]
        return rows[key]
    return DriveClient(gws_bin="gws", folder_cache=Path("/nonexistent"), runner=runner)


def test_folder_target_reads_name_and_path_and_refuses_a_shared_folder() -> None:
    folder = {"mimeType": "application/vnd.google-apps.folder", "trashed": False}
    rows: dict[str, object] = {
        FOLDER: {**folder, "name": "받은 첨부", "parents": ["P1"]},
        "P1": {**folder, "name": "연구", "parents": ["ROOT"]},
        "ROOT": {**folder, "name": "내 드라이브"},
        f"perm:{FOLDER}": {"permissions": [{"type": "user", "role": "owner"}]},
    }
    found = drive_outputs.folder_target(FOLDER, client=_drive(rows))
    assert (found.name, found.path) == ("받은 첨부", ("내 드라이브", "연구", "받은 첨부"))
    rows[f"perm:{FOLDER}"] = {"permissions": [{"type": "user", "role": "owner"},
                                              {"type": "user", "role": "reader"}]}
    with pytest.raises(DriveClientError, match="소유자 외"):
        drive_outputs.folder_target(FOLDER, client=_drive(rows))
    rows[FOLDER] = {"mimeType": "application/pdf", "name": "x.pdf"}
    with pytest.raises(DriveClientError, match="폴더가 아니"):
        drive_outputs.folder_target(FOLDER, client=_drive(rows))
    with pytest.raises(ValueError):
        drive_outputs.folder_target("../etc", client=_drive(rows))


def test_one_failed_file_leaves_the_rest_saved_and_the_next_tick_retries_only_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path, "a.pdf", "b.pdf", "c.pdf")
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_args(files), store))
    calls: list[str] = []
    down = {"b"}

    def publisher(kind, title, artifacts, *, on, project):  # noqa: ANN001, ANN202
        calls.append(title)
        if title in down:
            raise OSError("Drive 503")
        return Saved(f"https://drive.example/{title}")

    _approve(transport, record)
    first = _tick(store, transport, publisher)
    after = store.get(record.request_id) or record
    assert calls == ["a", "b", "c"] and first.done == () and after.status == "approved"
    assert [row["status"] for row in after.receipt["files"]] == ["ok", "failed", "ok"]
    assert "Drive 503" in after.receipt["files"][1]["error"]
    down.clear()
    second = _tick(store, transport, publisher)
    done = store.get(record.request_id) or record
    assert calls == ["a", "b", "c", "b"] and second.done == (record.request_id,) and done.status == "done"
    assert done.receipt["saved"] == 3 and done.attempts == 2
    text = notice_text(done, "done")
    assert "3개" in text and "2. ✅ 2026-11-13_b.pdf — https://drive.example/b" in text


def test_a_changed_frozen_file_is_final_while_the_others_are_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _files(tmp_path, "a.pdf", "b.pdf")
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_args(files), store))
    Path(str(record.payload["files"][0]["frozen_path"])).write_bytes(b"tampered")
    saved: list[str] = []
    _approve(transport, record)
    result = _tick(store, transport, lambda kind, title, arts, *, on, project: saved.append(title) or Saved("l"))
    failed = store.get(record.request_id) or record
    assert saved == ["b"] and result.failed == (record.request_id,) and failed.status == "failed"
    assert failed.receipt["saved"] == 1 and "바뀌었다" in failed.receipt["files"][0]["error"]
    assert "2개 중 1개 저장" in notice_text(failed, "failed")


def test_a_folder_batch_saves_into_that_folder_through_the_folder_saver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drive_outputs, "folder_target",
                        lambda folder_id: drive_outputs.FolderTarget(folder_id, "받은 첨부", ("받은 첨부",)))
    files = _files(tmp_path, "a.pdf", "b.pdf")
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_args(files, folder=FOLDER), store))
    landed: list[tuple[str, str, str]] = []

    def saver(local, name, folder_id, *, sha256, size):  # noqa: ANN001, ANN202
        landed.append((name, folder_id, sha256))
        return Saved(f"https://drive.example/{name}")

    _approve(transport, record)
    from automation.effect_request import gate
    from automation.effect_request.watch import run_tick
    runtime = gate.DiscordRuntime(transport, object(), "100")  # type: ignore[arg-type]
    run_tick(store, runtime, token="", now=__import__("datetime").datetime(2026, 11, 13),
             publisher=lambda *a, **k: pytest.fail("the tree publisher ran"), folder_saver=saver)
    assert [(n, f) for n, f, _ in landed] == [("a.pdf", FOLDER), ("b.pdf", FOLDER)]
    assert (store.get(record.request_id) or record).status == "done"


def test_save_now_batch_tries_the_failed_file_again_and_prints_one_line_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    files = _files(tmp_path, "a.pdf", "b.pdf")
    monkeypatch.setenv("EFFECT_REQUEST_ROOT", str(tmp_path / "state"))
    calls: list[str] = []

    def publish(kind, title, artifacts, *, on, project):  # noqa: ANN001, ANN202
        calls.append(title)
        if calls.count("b") == 1 and title == "b":
            raise OSError("Drive 500")
        return Saved(f"https://drive.example/{title}")

    monkeypatch.setattr(drive_outputs, "publish", publish)
    argv = ["save-now", "--summary", "첨부 저장", "--file", str(files[0]), "--file", str(files[1]),
            "--on", "2026-11-13", "--origin-channel-id", "1", "--origin-message-id", "2"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out.splitlines()
    assert calls == ["a", "b", "b"]
    assert out == ["OWNER-COMMAND-WRITTEN drive-save https://drive.example/a",
                   "OWNER-COMMAND-WRITTEN drive-save https://drive.example/b"]
    (record,) = EffectRequestStore(tmp_path / "state").all()
    assert record.status == "done" and record.attempts == 2


def test_save_now_batch_that_still_fails_exits_1_and_keeps_each_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    files = _files(tmp_path, "a.pdf", "b.pdf")
    monkeypatch.setenv("EFFECT_REQUEST_ROOT", str(tmp_path / "state"))

    def publish(kind, title, artifacts, *, on, project):  # noqa: ANN001, ANN202
        if title == "b":
            raise OSError("Drive 500")
        return Saved("https://drive.example/a")

    monkeypatch.setattr(drive_outputs, "publish", publish)
    argv = ["save-now", "--summary", "첨부 저장", "--file", str(files[0]), "--file", str(files[1]),
            "--on", "2026-11-13", "--origin-channel-id", "1", "--origin-message-id", "2"]
    assert cli.main(argv) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "OWNER-COMMAND-WRITTEN drive-save https://drive.example/a"
    assert out[1].startswith("DRIVE-SAVE-FAILED 2026-11-13_b.pdf OSError: Drive 500")
    (record,) = EffectRequestStore(tmp_path / "state").all()
    assert record.status == "failed" and record.attempts == cli.SAVE_NOW_ROUNDS
    assert [row["status"] for row in record.receipt["files"]] == ["ok", "failed"]
