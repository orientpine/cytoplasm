"""위키 frontmatter 스키마 v2 — entity / relations / event_date.

WHY: v1 은 판단의 **신뢰**만 타입화했다(kind/authority/provenance/status/review_after/
supersedes). 실패하던 질의는 신뢰가 아니라 **주어와 시점**이었다 — "김박사랑 언제 뭘
정했지". v2 는 그 둘을 붙인다: `entity` 는 검색 앵커, `event_date` 는 노트가 서술하는
사건의 실제 날짜(작성 시각과 다르다), `relations` 는 그 사이의 관계다.

`event_date` 는 이미 소비자가 기다리고 있다 — `automation/knowledge/rank.py`
`derive_doc_date` 가 이 키를 1순위로 읽는다. 지금까지 위키 노트는 그 자리에 아무것도
넣지 못해 항상 `updated`(작성 시각)로 정렬됐다.

`relations` 에 형식을 하나 두는 이유: 형식이 없으면 `entity` 와 구별할 수 없어
다음 사람이 무엇을 어디에 넣을지 알 수 없다. 닫힌 어휘는 만들지 않는다 — 술어는
자유롭고 모양만 고정한다.
"""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))

wiki_store = import_module("wiki_store")

_BODY = "본문 첫 줄\n"


def _v1_meta() -> dict[str, object]:
    return {
        "title": "지식 계층 결정",
        "tags": ["연구", "지식계층"],
        "created": "2026-08-21T00:00:00Z",
        "updated": "2026-08-21T00:00:00Z",
        "links": ["knowledge-layer"],
        "kind": "decision",
        "authority": "default",
        "provenance": "stated",
    }


def _v2_meta() -> dict[str, object]:
    return {
        **_v1_meta(),
        "entity": ["차백동", "한국기계연구원"],
        "relations": ["counterpart:김박사", "project:autophagy"],
        "event_date": "2026-08-21",
    }


def test_validate_meta_accepts_the_three_v2_keys() -> None:
    assert wiki_store.validate_meta(_v2_meta()) == []


def test_v2_keys_are_twin_keys_so_they_require_kind() -> None:
    assert wiki_store.validate_meta(_v2_meta()) == []
    meta = {key: value for key, value in _v2_meta().items() if key != "kind"}
    errors = wiki_store.validate_meta(meta)
    assert any("kind" in error for error in errors), errors


def test_entity_rejects_non_list_and_empty_items() -> None:
    assert wiki_store.validate_meta(_v2_meta()) == []
    assert any("entity" in e for e in wiki_store.validate_meta({**_v2_meta(), "entity": "차백동"}))
    assert any("entity" in e for e in wiki_store.validate_meta({**_v2_meta(), "entity": ["  "]}))


def test_relations_requires_the_predicate_target_shape() -> None:
    for bad in (["counterpart"], ["Counterpart:김박사"], ["counterpart:"], ["with space:x"]):
        errors = wiki_store.validate_meta({**_v2_meta(), "relations": bad})
        assert any("relations" in e for e in errors), (bad, errors)
    assert wiki_store.validate_meta({**_v2_meta(), "relations": ["a-b_c:대상"]}) == []


def test_event_date_is_a_calendar_day_not_a_timestamp() -> None:
    assert wiki_store.validate_meta(_v2_meta()) == []
    for bad in ("2026-08-21T00:00:00Z", "2026-13-01", "20260821"):
        errors = wiki_store.validate_meta({**_v2_meta(), "event_date": bad})
        assert any("event_date" in e for e in errors), (bad, errors)


def test_compose_note_round_trips_v2_keys() -> None:
    text = wiki_store.compose_note(_v2_meta(), _BODY)
    meta, body = wiki_store.parse_note(text)
    assert meta == _v2_meta()
    assert body == _BODY


def test_v2_keys_serialize_after_the_v1_twin_keys() -> None:
    text = wiki_store.compose_note(_v2_meta(), _BODY)
    keys = [line.split(":", 1)[0] for line in text.splitlines()[1:] if line != "---"]
    keys = [key for key in keys if key in wiki_store.TWIN_KEYS]
    assert keys == [
        "kind", "authority", "provenance", "entity", "relations", "event_date",
    ], keys


def test_legacy_v1_notes_serialize_byte_for_byte_unchanged() -> None:
    """v2 도입이 기존 28건을 다시 쓰게 만들면 안 된다."""
    text = wiki_store.compose_note(_v1_meta(), _BODY)
    assert "entity" not in text and "relations" not in text and "event_date" not in text
    meta, _ = wiki_store.parse_note(text)
    assert meta == _v1_meta()
