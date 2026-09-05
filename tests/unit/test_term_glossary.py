"""문서 종류별로 중첩된 교정 참고 문서 — 뿌리 · 문서 종류 · 과제 순으로 깊은 층이 이긴다."""

from __future__ import annotations

from pathlib import Path

import pytest

from automation import term_glossary


class _FakeDrive:
    """폴더 경로 → 파일 이름 → 내용. 없는 폴더는 None 을 돌려준다(생성하지 않는다)."""

    def __init__(self, tree: dict[tuple[str, ...], dict[str, str]]) -> None:
        self.tree = tree
        self.looked: list[tuple[str, ...]] = []
        self.created: list[tuple[str, ...]] = []

    def find_folder_path(self, parts):
        key = tuple(parts)
        self.looked.append(key)
        return "/".join(key) if key in self.tree else None

    def ensure_folder_path(self, parts):  # pragma: no cover - 호출되면 그 자체가 결함이다
        self.created.append(tuple(parts))
        raise AssertionError("교정 참고 문서를 찾는 일이 폴더를 만들어서는 안 된다")

    def list_children(self, folder):
        key = tuple(folder.split("/"))
        return [{"id": f"{folder}/{name}", "name": name} for name in self.tree.get(key, {})]

    def download_file(self, file_id: str, dest: Path) -> None:
        folder, _, name = file_id.rpartition("/")
        dest.write_text(self.tree[tuple(folder.split("/"))][name], encoding="utf-8")


def test_layers_nest_from_the_root_through_the_document_kind_to_the_project() -> None:
    assert term_glossary.layers("meeting", "해양고신뢰성") == (
        ("autophagy",),
        ("autophagy", "회의록"),
        ("autophagy", "회의록", "해양고신뢰성"),
    )


def test_layers_stop_at_the_document_kind_when_no_project_is_named() -> None:
    assert term_glossary.layers("transcript") == (("autophagy",), ("autophagy", "전사본"))


def test_a_document_kind_that_never_reaches_drive_still_has_a_folder() -> None:
    """lifelog 노트는 Drive 산출물이 아니지만 자기 교정 참고 문서를 갖는다."""
    assert term_glossary.layers("lifelog") == (("autophagy",), ("autophagy", "라이프로그"))


def test_an_unknown_document_kind_is_refused() -> None:
    with pytest.raises(term_glossary.TermGlossaryError):
        term_glossary.layers("없는종류")


def test_the_deeper_layer_overrides_the_same_term(tmp_path: Path) -> None:
    drive = _FakeDrive(
        {
            ("autophagy",): {"용어집.csv": "영무,업무\n한전기술\n"},
            ("autophagy", "회의록"): {"용어집.csv": "영무,업무추진\n"},
        }
    )

    pairs = term_glossary.glossary_for(
        "meeting", client=drive, env={"TERM_GLOSSARY_CACHE": str(tmp_path)}
    )

    assert dict(pairs) == {"영무": "업무추진", "한전기술": "한전기술"}
    assert drive.created == []


def test_the_drive_answer_is_cached_for_the_path_that_cannot_reach_drive(tmp_path: Path) -> None:
    """plaud 는 DRIVE_PUBLISH_ENABLED=0 으로 돈다 — 캐시가 없으면 한 낱말도 못 고친다."""
    drive = _FakeDrive({("autophagy",): {"용어집.csv": "영무,업무\n"}})
    env = {"TERM_GLOSSARY_CACHE": str(tmp_path)}

    term_glossary.glossary_for("lifelog", client=drive, env=env)
    offline = term_glossary.glossary_for("lifelog", env=env)

    assert dict(offline) == {"영무": "업무"}


def test_drive_is_never_consulted_without_the_opt_in(tmp_path: Path) -> None:
    assert term_glossary.glossary_for("meeting", env={"TERM_GLOSSARY_CACHE": str(tmp_path)}) == ()


def test_an_explicitly_configured_file_wins_outright(tmp_path: Path) -> None:
    path = tmp_path / "용어집.csv"
    path.write_text("영무,업무\n", encoding="utf-8")

    pairs = term_glossary.glossary_for(
        "meeting", env={"TERM_GLOSSARY_FILE": str(path), "TERM_GLOSSARY_CACHE": str(tmp_path)}
    )

    assert dict(pairs) == {"영무": "업무"}


def test_a_drive_that_will_not_answer_falls_back_to_the_cache(tmp_path: Path, capsys) -> None:
    env = {"TERM_GLOSSARY_CACHE": str(tmp_path)}
    term_glossary.glossary_for(
        "meeting", client=_FakeDrive({("autophagy",): {"용어집.csv": "영무,업무\n"}}), env=env
    )

    class _Broken:
        def find_folder_path(self, parts):
            raise RuntimeError("drive down")

    pairs = term_glossary.glossary_for("meeting", client=_Broken(), env=env)

    assert dict(pairs) == {"영무": "업무"}
    assert "GLOSSARY-FETCH-FAIL" in capsys.readouterr().err


def test_drive_answering_with_nothing_empties_the_cache(tmp_path: Path, capsys) -> None:
    env = {"TERM_GLOSSARY_CACHE": str(tmp_path)}
    term_glossary.glossary_for(
        "meeting", client=_FakeDrive({("autophagy",): {"용어집.csv": "영무,업무\n"}}), env=env
    )

    pairs = term_glossary.glossary_for("meeting", client=_FakeDrive({}), env=env)

    assert pairs == ()
    assert "GLOSSARY-DRIVE-ABSENT" in capsys.readouterr().err
    assert term_glossary.glossary_for("meeting", env=env) == ()


def test_the_legacy_txt_name_is_still_read(tmp_path: Path) -> None:
    drive = _FakeDrive({("autophagy",): {"용어집.txt": "영무=업무\n"}})

    pairs = term_glossary.glossary_for(
        "meeting", client=drive, env={"TERM_GLOSSARY_CACHE": str(tmp_path)}
    )

    assert dict(pairs) == {"영무": "업무"}
