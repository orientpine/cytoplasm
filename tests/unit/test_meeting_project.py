"""Drive-backed meeting form and action-item board contracts."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_action_db  # noqa: E402
import meeting_project  # noqa: E402


class FakeDrive:
    def __init__(self, children: dict[str, list[dict[str, str]]] | None = None) -> None:
        self.children = children or {}
        self.downloads: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, str, str]] = []

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str:
        folder = "/".join(parts)
        self.calls.append(("ensure_folder_path", folder))
        return folder

    def list_children(self, folder: str) -> list[dict[str, str]]:
        self.calls.append(("list_children", folder))
        return self.children.get(folder, [])

    def download_file(self, file_id: str, dest: Path) -> None:
        self.calls.append(("download_file", file_id))
        dest.write_text(self.downloads[file_id], encoding="utf-8")

    def upsert_file(self, local: Path, name: str, parent_id: str) -> dict[str, str]:
        self.uploads.append((name, parent_id, local.read_text(encoding="utf-8")))
        return {"id": name, "webViewLink": f"https://drive.test/{name}"}

    def verify_owner_only(self, file_id: str) -> None:
        self.calls.append(("verify_owner_only", file_id))

    def download_and_verify(self, file_id: str, local: Path) -> None:
        self.calls.append(("download_and_verify", file_id))


class RaisingDrive(FakeDrive):
    def ensure_folder_path(self, parts: tuple[str, ...]) -> str:
        raise RuntimeError("Drive unavailable")


def _drive_with(*children: tuple[str, str, str]) -> FakeDrive:
    project = "autophagy/회의록/해양고신뢰성"
    drive = FakeDrive({project: [{"id": file_id, "name": name} for file_id, name, _ in children]})
    drive.downloads = {file_id: text for file_id, _, text in children}
    return drive


def test_load_board_with_no_project_never_reaches_drive() -> None:
    drive = RaisingDrive()

    board = meeting_project.load_board("", client=drive)

    assert board == meeting_project.empty_board()
    assert drive.calls == []


def test_sensitive_board_never_reaches_drive() -> None:
    drive = RaisingDrive()

    board = meeting_project.load_board("해양고신뢰성", sensitive=True, client=drive)

    assert board == meeting_project.empty_board("해양고신뢰성")
    assert drive.calls == []


def test_load_board_parses_a_readable_form() -> None:
    drive = _drive_with(("form", "회의록양식.md", "회의록\n\n1. 일시\n2. 참석자\n"))

    board = meeting_project.load_board("해양고신뢰성", client=drive)

    assert board.template is not None
    assert board.template.title == "회의록"
    assert [section.slot for section in board.template.sections] == ["meta", "attendees"]


def test_unreadable_form_is_announced_to_the_owner(capsys) -> None:
    drive = _drive_with(("form", "양식.hwp", "binary form"))

    board = meeting_project.load_board("해양고신뢰성", client=drive)

    assert board.template is None
    assert "TEMPLATE-UNREADABLE project=해양고신뢰성 name=양식.hwp" in capsys.readouterr().err


def test_load_board_reads_existing_action_items() -> None:
    record = meeting_action_db.Record(
        "HOGS260001", "해양고신뢰성", "검토", "차", "2026-09-01", "open", "2026-08-27", "회의", "", "", "근거"
    )
    drive = _drive_with(("db", meeting_action_db.DB_FILENAME, meeting_action_db.dump((record,))))

    board = meeting_project.load_board("해양고신뢰성", client=drive)

    assert board.records == (record,)


def test_load_board_reduces_drive_failures_to_an_empty_board(capsys) -> None:
    board = meeting_project.load_board("해양고신뢰성", client=RaisingDrive())

    assert board == meeting_project.empty_board("해양고신뢰성")
    assert "BOARD-FETCH-FAIL project=해양고신뢰성 RuntimeError" in capsys.readouterr().err


def test_save_board_writes_database_without_rewriting_an_unchanged_registry() -> None:
    record = meeting_action_db.Record(
        "HOGS260001", "해양고신뢰성", "검토", "차", "", "open", "2026-08-27", "회의", "", "", "근거"
    )
    board = meeting_project.Board("해양고신뢰성", "HOGS", None, (), (("HOGS", "해양고신뢰성"),), False)
    drive = FakeDrive()

    db_link, registry_link = meeting_project.save_board(board, (record,), client=drive)

    assert db_link == "https://drive.test/action-items.csv"
    assert registry_link == ""
    assert [name for name, _, _ in drive.uploads] == [meeting_action_db.DB_FILENAME]
    assert "HOGS260001" in drive.uploads[0][2]


_FOLDER_MIME = "application/vnd.google-apps.folder"


def _category_drive(*names: str) -> FakeDrive:
    return FakeDrive({
        "autophagy/회의록": [
            {"id": name, "name": name, "mimeType": _FOLDER_MIME} for name in names
        ]
    })


def test_detect_project_matches_an_existing_project_folder() -> None:
    drive = _category_drive("해양고신뢰성", "자율굴착기")

    assert meeting_project.detect_project("20260825_해양고신뢰성.md", client=drive) == "해양고신뢰성"


def test_detect_project_invents_nothing_when_no_folder_matches() -> None:
    """지어낸 과제명은 없는 폴더를 만들고 원장을 엉뚱한 곳에 쓴다 — 그럴 바엔 과제 없이 간다."""
    drive = _category_drive("해양고신뢰성")

    assert meeting_project.detect_project("주간 정례회의.md", client=drive) == ""


def test_detect_project_takes_the_longest_nested_match() -> None:
    drive = _category_drive("해양고신뢰성", "고신뢰성")

    assert meeting_project.detect_project("20260825_해양고신뢰성.md", client=drive) == "해양고신뢰성"


def test_detect_project_refuses_two_unrelated_matches() -> None:
    """틀린 과제 폴더에 원장을 쓰는 것이 과제 없이 가는 것보다 나쁘다."""
    drive = _category_drive("해양고신뢰성", "자율굴착기")

    assert meeting_project.detect_project("해양고신뢰성-자율굴착기 합동회의.md", client=drive) == ""


def test_detect_project_reaches_no_drive_without_the_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)

    assert meeting_project.detect_project("해양고신뢰성.md") == ""


def test_detect_project_reduces_a_drive_failure_to_no_project(capsys) -> None:
    assert meeting_project.detect_project("해양고신뢰성.md", client=RaisingDrive()) == ""
    assert "PROJECT-DETECT-FAIL" in capsys.readouterr().err


def _tree(**folders: list[dict[str, str]]) -> FakeDrive:
    return FakeDrive(dict(folders))


def _folder(name: str, ident: str) -> dict[str, str]:
    return {"id": ident, "name": name, "mimeType": _FOLDER_MIME}


def _pending_drive(*minutes: str) -> FakeDrive:
    """전사본 1건 · 회의록 폴더에는 인자로 준 이름들만 있다."""
    return _tree(**{
        "autophagy/전사본": [_folder("해양고신뢰성", "t-proj")],
        "t-proj": [_folder("2026", "t-2026"), {"id": "gl", "name": "용어집.txt"}],
        "t-2026": [{"id": "tr", "name": "2026-08-26_20260825_해양고신뢰성.md"},
                   {"id": "note", "name": "메모.txt"}],
        "autophagy/회의록": [_folder("해양고신뢰성", "m-proj")],
        "m-proj": [_folder("2026", "m-2026")],
        "m-2026": [{"id": f"m{i}", "name": name} for i, name in enumerate(minutes)],
    })


def test_pending_transcripts_reports_a_transcript_with_no_minutes() -> None:
    pending = meeting_project.pending_transcripts(client=_pending_drive())

    assert [(item.project, item.year, item.name) for item in pending] == [
        ("해양고신뢰성", "2026", "2026-08-26_20260825_해양고신뢰성.md")
    ]
    assert pending[0].stem == "2026-08-26_20260825_해양고신뢰성"


def test_pending_transcripts_skips_one_that_already_has_minutes() -> None:
    """대응은 이름으로 본다 — 전사본이 다시 다듬어져 발행돼도 같은 회의다."""
    drive = _pending_drive("2026-08-25_회의록-2026-08-26_20260825_해양고신뢰성.md")

    assert meeting_project.pending_transcripts(client=drive) == ()


def test_pending_transcripts_ignores_files_that_are_not_transcripts() -> None:
    pending = meeting_project.pending_transcripts(client=_pending_drive())

    assert all(item.name.endswith(".md") for item in pending)
    assert "용어집.txt" not in [item.name for item in pending]


def test_pending_transcripts_reaches_no_drive_without_the_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)

    assert meeting_project.pending_transcripts() == ()


def test_pending_transcripts_reduces_a_drive_failure_to_nothing(capsys) -> None:
    assert meeting_project.pending_transcripts(client=RaisingDrive()) == ()
    assert "PENDING-SCAN-FAIL" in capsys.readouterr().err
