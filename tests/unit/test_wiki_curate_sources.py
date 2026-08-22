"""원천 읽기 — Obsidian 노트를 후보 입력으로, 위키 본문을 중복 차단 지문으로."""

from __future__ import annotations

from pathlib import Path

from automation.wiki_curate.sources import read_obsidian_notes, read_wiki_digests
from automation.wiki_curate.candidates import content_digest

_NOTE = (
    "---\n"
    'title: "KIMM 협업"\n'
    "tags: [연구, 협업]\n"
    "entity: [김박사, 한국기계연구원]\n"
    "event_date: 2026-05-02\n"
    "---\n"
    "조건을 합의했다.\n"
)


def test_obsidian_notes_become_source_notes(tmp_path: Path) -> None:
    root = tmp_path / "obsidian"
    (root / "projects").mkdir(parents=True)
    (root / "projects" / "kimm.md").write_text(_NOTE, encoding="utf-8")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "skip.md").write_text(_NOTE, encoding="utf-8")

    notes = read_obsidian_notes(root, classifier=lambda text: frozenset())
    assert [note.ref for note in notes] == ["projects/kimm.md"]
    note = notes[0]
    assert note.title == "KIMM 협업"
    assert note.entities == ("김박사", "한국기계연구원")
    assert note.event_date == "2026-05-02"
    assert note.tags == ("연구", "협업")
    assert note.body.strip() == "조건을 합의했다."
    assert note.sensitivity is None


def test_the_classifier_decides_sensitivity(tmp_path: Path) -> None:
    root = tmp_path / "obsidian"
    root.mkdir()
    (root / "patent.md").write_text(_NOTE, encoding="utf-8")
    notes = read_obsidian_notes(root, classifier=lambda text: frozenset({"patent-sensitive"}))
    assert notes[0].sensitivity == "patent-sensitive"


def test_wiki_bodies_become_dedup_digests(tmp_path: Path) -> None:
    vault = tmp_path / "wiki"
    vault.mkdir()
    (vault / "a.md").write_text(_NOTE, encoding="utf-8")
    assert read_wiki_digests(vault) == frozenset({content_digest("조건을 합의했다.")})


def test_defaults_keep_runtime_state_outside_the_checkout() -> None:
    from automation.wiki_curate.cli import DEFAULT_WORKSPACE, build_parser
    from automation.wiki_curate.state import DEFAULT_STATE_PATH

    args = build_parser().parse_args(["--obsidian-root", "/tmp/obsidian"])
    assert args.state == DEFAULT_STATE_PATH
    assert str(DEFAULT_STATE_PATH).startswith("~/.hermes/")
    assert str(DEFAULT_WORKSPACE).startswith("~/.hermes/")
    assert args.emit is False, "기본은 계획 출력 — 게이트 호출은 명시 플래그가 있어야 한다"


def test_wiki_relations_reveal_which_sources_are_already_curated(tmp_path: Path) -> None:
    from automation.wiki_curate.sources import read_wiki_origins

    vault = tmp_path / "wiki"
    vault.mkdir()
    (vault / "curated.md").write_text(
        "---\n"
        'title: "요약본"\n'
        "tags: [연구]\n"
        "relations: [source:projects/kimm.md, counterpart:김박사]\n"
        "---\n"
        "증류된 본문 — 원천과 다르다.\n",
        encoding="utf-8",
    )
    assert read_wiki_origins(vault) == frozenset({"projects/kimm.md"})
