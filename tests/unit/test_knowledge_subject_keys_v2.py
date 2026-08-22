"""v2 subject 키가 인제스트와 파사드까지 실제로 흐르는가.

스키마에 키를 더하는 것만으로는 아무 일도 일어나지 않는다. 세 지점이 함께 움직여야
질의가 달라진다: 인제스트가 RAG 메타로 실어야 하고, 위키 어댑터가 `entity` 를 매칭
대상으로 봐야 하며, 랭킹이 `event_date` 를 노트 날짜로 써야 한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from automation.knowledge.adapters.wiki import fetch_wiki
from automation.knowledge.pack import KnowledgeQuery
from automation.rag_ingest.sources.files import scan_directory

_REPO = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO / "tests" / "fixtures" / "knowledge"
_NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def _note(*, title: str, tags: str, body: str, extra: str) -> str:
    return (
        "---\n"
        f'title: "{title}"\n'
        f"tags: [{tags}]\n"
        "created: 2026-08-21T00:00:00Z\n"
        "updated: 2026-08-21T00:00:00Z\n"
        "links: []\n"
        "kind: decision\n"
        "authority: default\n"
        "provenance: stated\n"
        f"{extra}"
        "---\n"
        f"{body}\n"
    )


def _vault(tmp_path: Path, name: str, text: str) -> dict[str, str]:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / f"{name}.md").write_text(text, encoding="utf-8")
    return {
        "WIKI_SCRIPTS": str(_REPO / "skills" / "wiki" / "scripts"),
        "WIKI_ROOT": str(vault),
        "KNOWLEDGE_SENSITIVITY_RULES": str(_FIXTURES / "sensitivity-rules.yaml"),
    }


def test_ingest_carries_the_v2_subject_keys_into_rag_metadata(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "decision.md").write_text(
        _note(
            title="지식 계층 결정",
            tags="연구",
            body="본문",
            extra="entity: [차백동, 한국기계연구원]\nrelations: [counterpart:김박사]\nevent_date: 2026-05-02\n",
        ),
        encoding="utf-8",
    )
    documents, _ = scan_directory(root, "wiki", "wiki", {}, 2000)
    metadata = documents[0].chunks[0].metadata
    assert metadata["entity"] == "차백동,한국기계연구원"
    assert metadata["relations"] == "counterpart:김박사"
    assert metadata["event_date"] == "2026-05-02"


def test_wiki_adapter_anchors_on_entity_not_only_on_prose(tmp_path: Path) -> None:
    """이름이 본문·제목·태그 어디에도 없고 entity 에만 있어도 찾혀야 한다."""
    env = _vault(
        tmp_path,
        "anchor",
        _note(title="분기 계획", tags="연구", body="합의한 내용을 적는다.", extra="entity: [김박사]\n"),
    )
    result = fetch_wiki(
        KnowledgeQuery("김박사", purpose="cite", sources=frozenset({"wiki"})), _NOW, env
    )
    assert [item.ref for item in result.wiki_items] == ["anchor"]


def test_wiki_evidence_is_dated_by_the_event_not_the_authoring_time(tmp_path: Path) -> None:
    env = _vault(
        tmp_path,
        "dated",
        _note(title="배양 조건 결정", tags="연구", body="배양 조건을 정했다.", extra="event_date: 2026-05-02\n"),
    )
    result = fetch_wiki(
        KnowledgeQuery("배양", purpose="cite", sources=frozenset({"wiki"})), _NOW, env
    )
    item = result.wiki_items[0]
    assert (item.doc_date, item.date_basis) == ("2026-05-02", "day")
