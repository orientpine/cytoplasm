"""DRIVE_LAYOUT=para: every automatic save lands in the owner's PARA tree (owner 2026-11-01 Q427-Q429).

Kept apart from test_drive_taxonomy.py and test_drive_outputs.py, which pin the legacy
layout a node keeps until its folders were moved: this file owns the PARA side and the
one-release read-only fallback to the legacy folders.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from automation import drive_outputs, drive_reference, drive_taxonomy, term_glossary
from automation.project_board import minutes
from automation.yoodori_gate import yoodori_request_runtime
from skills.proposal.scripts import proposal_publish

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "meeting" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "mail" / "scripts"))

import mail_attachment_archive  # noqa: E402
import meeting_project  # noqa: E402

_FOLDER = "application/vnd.google-apps.folder"


class TreeDrive:
    """An in-memory My Drive. ``find`` never creates; ``ensure`` records what it creates."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, str]] = {"root": {"name": "", "parent": "", "mimeType": _FOLDER}}
        self.bytes: dict[str, bytes] = {}
        self.created: list[tuple[str, ...]] = []
        self._next = 0

    def _child(self, parent: str, name: str) -> str | None:
        return next((i for i, n in self.nodes.items() if n["parent"] == parent and n["name"] == name), None)

    def _add(self, parent: str, name: str, mime: str) -> str:
        self._next += 1
        node_id = f"id{self._next:08d}"
        self.nodes[node_id] = {"name": name, "parent": parent, "mimeType": mime}
        return node_id

    def folder(self, *parts: str) -> str:
        current = "root"
        for part in parts:
            current = self._child(current, part) or self._add(current, part, _FOLDER)
        return current

    def file(self, parts: tuple[str, ...], name: str, payload: bytes = b"x") -> str:
        file_id = self._add(self.folder(*parts), name, "text/markdown")
        self.bytes[file_id] = payload
        return file_id

    def path_of(self, node_id: str) -> str:
        names: list[str] = []
        while node_id != "root":
            names.append(self.nodes[node_id]["name"])
            node_id = self.nodes[node_id]["parent"]
        return "/".join(reversed(names))

    def files(self) -> set[str]:
        return {self.path_of(i) for i, n in self.nodes.items() if n["mimeType"] != _FOLDER}

    def find_folder_path(self, parts: tuple[str, ...]) -> str | None:
        current: str | None = "root"
        for part in parts:
            current = self._child(current, part) if current else None
        return current

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str:
        if self.find_folder_path(tuple(parts)) is None:
            self.created.append(tuple(parts))
        return self.folder(*parts)

    def list_children(self, folder_id: str) -> list[dict[str, str]]:
        return [
            {"id": i, "name": n["name"], "mimeType": n["mimeType"], "modifiedTime": i}
            for i, n in self.nodes.items() if n["parent"] == folder_id
        ]

    def find_file(self, name: str, parent_id: str) -> str | None:
        return self._child(parent_id, name)

    def upsert_file(self, local: Path, name: str, parent_id: str, prior_id: str | None = None):
        existing = self._child(parent_id, name)
        file_id = existing or self._add(parent_id, name, "text/markdown")
        self.bytes[file_id] = local.read_bytes()
        return {"id": file_id, "webViewLink": f"https://drive.test/{file_id}",
                "action": "updated" if existing else "created"}

    def verify_owner_only(self, file_id: str) -> None:
        return None

    def download_and_verify(self, file_id: str, local: Path) -> str:
        import hashlib

        return hashlib.sha256(self.bytes[file_id]).hexdigest()

    def download_file(self, file_id: str, dest: Path, *, export_as: str = "") -> str:
        dest.write_bytes(self.bytes[file_id])
        return str(dest)


@pytest.fixture
def para(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIVE_LAYOUT", "para")
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", "Dori's Home")


def _legacy_created(drive: TreeDrive) -> list[tuple[str, ...]]:
    return [parts for parts in drive.created if parts and parts[0] == "Dori's Home"]


@pytest.mark.parametrize(
    ("kind", "project", "expected"),
    [
        ("meeting", "데모과제", ("1_Project", "2026_데모과제", "회의록")),
        ("meeting", None, ("0_Inbox", "분류대기", "2026")),
        ("transcript", "데모과제", ("4_Archive", "전사본", "2026")),
        ("audio", "lifelog", ("4_Archive", "녹음원본", "2026")),
        ("share", None, ("4_Archive", "공유한-파일", "2026")),
        ("report", None, ("2_Area", "연구동향", "주간동향", "2026")),
        ("doctype", "시연 과제", ("1_Project", "2026_시연-과제", "문서")),
        ("proposal", None, ("0_Inbox", "분류대기", "2026")),
        ("procurement", None, ("2_Area", "행정-결재", "구매", "2026")),
    ],
)
def test_each_kind_lands_in_its_para_place(para: None, kind: str, project: str | None, expected) -> None:
    assert drive_taxonomy.folder_parts(kind, 2026, project=project) == expected


def test_without_the_switch_the_legacy_tree_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DRIVE_LAYOUT", raising=False)
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", "Dori's Home")
    assert drive_taxonomy.folder_parts("meeting", 2026, project="데모과제") == (
        "Dori's Home", "회의록", "데모과제", "2026")
    assert drive_taxonomy.share_root() == ("Dori's Home", "공유")
    assert mail_attachment_archive.root_parts() == ("Dori's Home", "메일 첨부파일")


def test_a_project_keeps_the_folder_of_the_year_it_started(para: None) -> None:
    existing = ["2025_데모과제", "2026_시연-과제", "회의록-모음"]
    assert drive_taxonomy.project_folder_name("데모과제", year=2027, existing=existing) == "2025_데모과제"
    assert drive_taxonomy.project_folder_name("시연 과제", year=2027, existing=existing) == "2026_시연-과제"
    assert drive_taxonomy.project_folder_name("새과제", year=2027, existing=existing) == "2027_새과제"
    with pytest.raises(drive_taxonomy.TaxonomyError):
        drive_taxonomy.project_slug("a/b")


def test_minutes_go_into_the_existing_project_folder_and_keep_their_date(para: None, tmp_path: Path) -> None:
    drive = TreeDrive()
    drive.folder("1_Project", "2025_데모과제", "회의록")
    note = tmp_path / "note.md"
    note.write_text("회의록 본문\n", encoding="utf-8")

    drive_outputs.publish("meeting", "회의록-정기회의", [(note, "회의록-정기회의")],
                          on=date(2026, 3, 2), project="데모과제", client=drive)
    note.write_text("고친 회의록\n", encoding="utf-8")
    again = drive_outputs.publish("meeting", "회의록-정기회의", [(note, "회의록-정기회의")],
                                  on=date(2026, 3, 9), project="데모과제", client=drive)

    assert drive.files() == {"1_Project/2025_데모과제/회의록/2026-03-02_회의록-정기회의.md"}
    assert again.action == "updated"
    assert _legacy_created(drive) == []


def test_a_transcript_is_archived_by_year_with_the_project_in_its_name(para: None, tmp_path: Path) -> None:
    drive = TreeDrive()
    text = tmp_path / "t.md"
    text.write_text("전사\n", encoding="utf-8")

    drive_outputs.publish("transcript", "정기회의", [(text, "정기회의")],
                          on=date(2026, 10, 20), project="데모과제", client=drive)

    assert drive.files() == {"4_Archive/전사본/2026/2026-10-20_데모과제_정기회의.md"}
    assert _legacy_created(drive) == []


def test_a_weekly_report_bundle_fits_the_para_depth(para: None, tmp_path: Path) -> None:
    drive = TreeDrive()
    body = tmp_path / "r.md"
    body.write_text("동향\n", encoding="utf-8")

    drive_outputs.publish("report", "주간동향", [(body, "주간동향")], on=date(2026, 11, 2), client=drive)

    assert drive.files() == {"2_Area/연구동향/주간동향/2026/2026-W45_주간동향/2026-W45_주간동향.md"}


def test_a_share_is_frozen_under_the_para_archive_and_a_legacy_card_is_refused(para: None) -> None:
    parts, name = drive_outputs.share_target("발표.pdf", on=date(2026, 11, 1))
    named, _ = drive_outputs.share_target("발표.pdf", on=date(2026, 11, 1), project="시연-과제")

    assert parts == ("4_Archive", "공유한-파일", "2026")
    assert named == ("4_Archive", "공유한-파일", "시연-과제", "2026")
    assert name == "2026-11-01_발표.pdf"
    drive_outputs.require_share_folder(parts)
    with pytest.raises(drive_outputs.ShareRefused):
        drive_outputs.require_share_folder(("Dori's Home", "공유", "2026"))


def test_mail_attachments_archive_under_4_archive(para: None) -> None:
    assert mail_attachment_archive.root_parts() == ("4_Archive", "메일 첨부파일")


def test_glossary_reads_legacy_then_para_and_writes_only_para(para: None, tmp_path: Path) -> None:
    drive = TreeDrive()
    drive.folder("1_Project", "2025_데모과제")
    drive.file(("Dori's Home", "전사본", "데모과제"), "용어집.csv", "영무,업무\n가나닥,가나다\n".encode())
    drive.file(("1_Project", "2025_데모과제"), "용어집.csv", "영무,업무추진\n".encode())
    env = {"TERM_GLOSSARY_CACHE": str(tmp_path / "cache"), "DRIVE_LAYOUT": "para"}

    pairs = dict(term_glossary.glossary_for("transcript", "데모과제", client=drive, env=env))
    action = term_glossary.add_pair("meeting", "데모", "데모과제", project="데모과제",
                                    client=drive, env=env)

    assert pairs == {"영무": "업무추진", "가나닥": "가나다"}
    assert action == "added"
    assert drive.bytes[drive.find_file("용어집.csv", drive.find_folder_path(
        ("1_Project", "2025_데모과제")))].decode().splitlines()[-1] == "데모,데모과제"
    assert _legacy_created(drive) == []


def test_minutes_scan_reads_project_folders_and_what_is_left_in_the_legacy_tree(para: None) -> None:
    drive = TreeDrive()
    new = drive.file(("1_Project", "2026_시연-과제", "회의록"), "2026-10-29_회의록-시연.md")
    old = drive.file(("Dori's Home", "회의록", "데모과제", "2026"), "2026-08-26_회의록-착수.md")
    drive.file(("1_Project", "2026_시연-과제"), "2026-10-29_발표.md")

    found = {(item.file_id, item.project, item.year) for item in minutes.scan_outputs(drive)}

    assert found == {(new, "시연-과제", "2026"), (old, "데모과제", "2026")}
    assert drive.created == []


def test_pending_transcripts_follow_the_para_names(para: None) -> None:
    drive = TreeDrive()
    drive.folder("1_Project", "2026_데모과제", "회의록")
    drive.file(("1_Project", "2026_데모과제", "회의록"), "2026-10-20_회의록-2026-10-20_정기회의.md")
    drive.file(("4_Archive", "전사본", "2026"), "2026-10-20_데모과제_정기회의.md")
    waiting = drive.file(("4_Archive", "전사본", "2026"), "2026-10-27_데모과제_후속회의.md")
    drive.file(("4_Archive", "전사본", "2026"), "2026-10-28_잡담_녹음.md")

    pending = meeting_project.pending_transcripts(client=drive)

    assert [(p.project, p.year, p.file_id) for p in pending] == [("데모과제", "2026", waiting)]
    assert drive.created == []


def test_the_project_board_reads_the_legacy_registry_without_creating_it(para: None) -> None:
    drive = TreeDrive()
    drive.folder("1_Project", "2026_데모과제", "회의록")
    drive.file(("Dori's Home", "회의록"), "project-codes.csv", "code,project\nHG,데모과제\n".encode())

    board = meeting_project.load_board("데모과제", client=drive)

    assert board.code == "HG"
    assert _legacy_created(drive) == []


def test_reference_root_is_the_projects_and_falls_back_to_kimm() -> None:
    drive = TreeDrive()
    drive.folder("KIMM", "Project")
    env = {"DRIVE_LAYOUT": "para", "DRIVE_PUBLISH_ENABLED": "1"}

    assert drive_reference.root_parts(env) == ("1_Project",)
    assert drive_reference.collect("굴착기 제안", client=drive, env=env).root == "KIMM"
    drive.folder("1_Project")
    assert drive_reference.collect("굴착기 제안", client=drive, env=env).root == "1_Project"
    assert drive.created == []


def test_a_proposal_version_goes_to_the_project_that_already_holds_it(para: None, tmp_path: Path) -> None:
    drive = TreeDrive()
    drive.folder("1_Project", "2026_굴착과제", "제안서", "demo")

    assert proposal_publish._proposal_root(drive, "demo") == ("1_Project", "2026_굴착과제", "제안서")
    assert proposal_publish._proposal_root(drive, "other") == ("0_Inbox", "분류대기", "제안서")


def test_the_yoodori_card_env_carries_the_layout(tmp_path: Path) -> None:
    (tmp_path / ".env.secrets").write_text('DRIVE_LAYOUT="para"\nOTHER=1\n', encoding="utf-8")

    values = yoodori_request_runtime.card_env(tmp_path, tmp_path, {})

    assert values["DRIVE_LAYOUT"] == "para"
    assert "OTHER" not in values


def test_the_legacy_migration_refuses_a_para_node(capsys: pytest.CaptureFixture[str]) -> None:
    from automation import drive_migrate_outputs

    assert drive_migrate_outputs.run([], client=TreeDrive(), environ={"DRIVE_LAYOUT": "para"}) == 2
    assert "PARA" in capsys.readouterr().err

