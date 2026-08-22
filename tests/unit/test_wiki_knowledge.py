from __future__ import annotations

import argparse
import json
import re
import sys
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "wiki" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from automation.knowledge.pack import EvidenceItem, EvidencePack, KnowledgeQuery, Store, Verdict  # noqa: E402
wiki_cli = import_module("wiki_cli")
wiki_gate = import_module("wiki_gate")
wiki_store = import_module("wiki_store")


def _item(
    item_id: str = "E1", *, store: str = "wiki", authority: str | None = "default",
    expired: bool | None = False, content: str = "승인된 예산 판단",
    sensitivity: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        item_id, cast(Store, store), "twin" if store == "wiki" else "conversation",
        "budget-rule" if store == "wiki" else "conversation/42", "예산 판단",
        "2026-08-18", "updated" if store == "wiki" else "day", 0.9, True,
        authority, expired, sensitivity, content, item_id.lower() * 32,
    )


def _pack(
    verdict: Verdict = "hit", *, items: tuple[EvidenceItem, ...] | None = None,
    conflict: bool = False,
) -> EvidencePack:
    query = KnowledgeQuery(
        "budget 배포 판단", "judgment", tags=frozenset({"budget"}), caller="wiki"
    )
    selected = ((_item(), _item("E2", store="rag", authority=None, expired=None))
                if items is None else items) if verdict == "hit" else ()
    layers = {
        "rag": "hit" if verdict == "hit" else "no_memory" if verdict == "no_evidence" else "unavailable",
        "wiki": "hit" if verdict == "hit" else "none" if verdict == "no_evidence" else "unavailable",
        "twin": "conflict" if conflict else "ok" if verdict == "hit" else "none" if verdict == "no_evidence" else "unavailable",
    }
    return EvidencePack("knowledge-v1", query, verdict, selected, layers)


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "wiki"
    gate = tmp_path / "gate"
    root.mkdir()
    monkeypatch.setattr(wiki_cli, "WIKI_ROOT", root)
    monkeypatch.setattr(wiki_gate, "GATE_DIR", gate)
    monkeypatch.setenv("WIKI_GATE_DIR", str(gate))
    return root, gate


def _draft_args() -> argparse.Namespace:
    return argparse.Namespace(
        edit=None, title="예산 대화 요약", tags="budget", links=None, slug="budget-summary",
        body="대화에서 정한 예산 원칙 [E1], 잘못된 인용 [E9].", body_file=None,
        stdin=False, channel_id="dm", kind=None, authority=None, provenance=None,
        status=None, review_after=None, supersedes=None, with_evidence=True,
    )


def test_consult_collects_facade_once_with_deterministic_vault_tags_and_labels_layers(
    roots: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = roots
    meta = {
        "title": "Budget", "tags": ["budget", "deploy"],
        "created": "2026-08-01T00:00:00Z", "updated": "2026-08-01T00:00:00Z",
        "links": [],
    }
    (root / "budget.md").write_text(wiki_store.compose_note(meta, "seed"), encoding="utf-8")
    calls: list[tuple[str, frozenset[str]]] = []

    def collect(text: str, tags: frozenset[str], *, limit: int = 8) -> EvidencePack:
        calls.append((text, tags))
        return _pack()

    monkeypatch.setattr(wiki_cli.wiki_evidence.wiki_knowledge, "collect", collect)
    args = argparse.Namespace(text="budget 선택 unknown", limit=8, json=False)

    assert wiki_cli.cmd_consult(args) == 0

    output = capsys.readouterr().out
    assert calls == [("budget 선택 unknown", frozenset({"budget"}))]
    assert "[위키 규칙] [E1] wiki: budget-rule" in output
    assert "[RAG 선례] [E2] RAG/대화: conversation/42" in output


def test_draft_hit_pack_validates_citations_appends_sources_and_writes_sidecar(
    roots: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, gate = roots

    assert wiki_cli.cmd_draft(_draft_args(), evidence_pack=_pack()) == 0

    output = capsys.readouterr().out
    draft_id = re.search(r"DRAFT-CREATED id=(\w+)", output)
    assert draft_id is not None
    record = wiki_gate.load_draft(draft_id.group(1))
    _, body = wiki_store.parse_note(record["note_text"])
    assert "[E1]" in body and "[E9]" not in body
    assert "## Sources" in body
    assert "[E1] wiki: budget-rule (principle, authority=default, updated 2026-08-18)" in body
    sidecar = gate / "evidence" / f"{draft_id.group(1)}.evidence.json"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["verdict"] == "hit"
    assert sidecar.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("verdict", "message"),
    (("no_evidence", "근거 없음"), ("unavailable", "근거 수집 불가")),
)
def test_non_hit_pack_adds_deterministic_verdict_and_draft_continues(
    verdict: Verdict, message: str, roots: tuple[Path, Path],
) -> None:
    assert wiki_cli.cmd_draft(_draft_args(), evidence_pack=_pack(verdict)) == 0
    records = wiki_gate.list_drafts()
    assert len(records) == 1
    _, body = wiki_store.parse_note(records[0]["note_text"])
    assert message in body


def test_sensitive_expired_advisory_evidence_is_read_only_and_marked_uncertain(
    roots: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = roots
    sensitive = _item(
        authority="advisory", expired=True,
        content="[[PATENT-SENSITIVE-RECALL]] 비공개 판단", sensitivity="patent-sensitive",
    )
    before = tuple(root.iterdir())

    args = argparse.Namespace(text="budget 판단", limit=8, json=False)
    assert wiki_cli.cmd_consult(args, evidence_pack=_pack(items=(sensitive,))) == 0

    assert "[불확실·충돌] [E1] wiki: budget-rule" in capsys.readouterr().out
    assert tuple(root.iterdir()) == before


def test_consult_json_exposes_only_count_and_layers(
    roots: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(text="budget 판단", limit=8, json=True)
    pack = _pack()
    assert wiki_cli.cmd_consult(args, evidence_pack=pack) == 0
    assert json.loads(capsys.readouterr().out) == {
        "evidence_count": 2, "layers": pack.layers,
    }
