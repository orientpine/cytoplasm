from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from skills.proposal.scripts import proposal_cli
from skills.proposal.scripts.proposal_figure_tokens import FigureTokenError, place_figures
from skills.proposal.scripts.proposal_ir import FIG_TOKEN_RE, FigureSpec, figures_to_json
from skills.proposal.scripts.proposal_version import Staging, VersionStore


def _version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proposals"
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(str(root).encode()).hexdigest())
    assert isinstance(staging, Staging)
    version = store.promote("demo", staging, {"parent": None, "schema_version": 1})
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    return root / "demo" / "versions" / version


def _plan(version: Path, placements: list[tuple[str, str]]) -> None:
    figures = [
        FigureSpec(figure_id, section_id, ("public:c",), "diagram", "caption", "", index)
        for index, (figure_id, section_id) in enumerate(placements)
    ]
    _ = (version / "figures.json").write_text(figures_to_json(figures), encoding="utf-8")


def _drafts(version: Path, bodies: dict[str, str]) -> Path:
    path = version / "out" / "drafts.json"
    sections = [{"section_id": key, "body": body} for key, body in bodies.items()]
    _ = path.write_text(json.dumps({"sections": sections}), encoding="utf-8")
    return path


def _tokens(path: Path) -> dict[str, list[str]]:
    document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    return {
        cast(str, section["section_id"]): FIG_TOKEN_RE.findall(cast(str, section["body"]))
        for section in cast(list[dict[str, object]], document["sections"])
    }


def test_engine_drafts_gain_one_token_per_planned_figure_in_band_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    version = _version(tmp_path, monkeypatch)
    _plan(version, [("fig-s0-01", "0"), ("fig-s1-01", "1"), ("fig-s1-02", "1")])
    drafts = _drafts(
        version,
        {"0": "첫 문단이다.\n\n둘째 문단이다.", "1": "가 문단이다.\n\n나 문단이다.\n\n다 문단이다.", "2": "그림 없는 절이다."},
    )
    stale = version / "out" / "drafts.refined.json"
    _ = stale.write_text("{}", encoding="utf-8")

    rc = proposal_cli.main(["figures", "--slug", "demo", "--json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["figures"] == {"0": 1, "1": 2}
    assert _tokens(drafts) == {"0": ["fig-s0-01"], "1": ["fig-s1-01", "fig-s1-02"], "2": []}
    assert drafts.stat().st_mode & 0o777 == 0o600
    assert not stale.exists()


def test_placing_twice_is_idempotent_and_replanning_moves_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _plan(version, [("fig-s0-01", "0")])
    drafts = _drafts(version, {"0": "첫 문단이다.\n\n둘째 문단이다."})
    _ = place_figures(version)
    once = drafts.read_bytes()

    _ = place_figures(version)
    assert drafts.read_bytes() == once

    _plan(version, [("fig-s0-01", "0"), ("fig-s0-02", "0")])
    _ = place_figures(version)
    assert _tokens(drafts) == {"0": ["fig-s0-01", "fig-s0-02"]}


def test_a_figure_for_a_section_the_drafts_lack_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _plan(version, [("fig-s9-01", "9")])
    drafts = _drafts(version, {"0": "본문이다."})
    before = drafts.read_bytes()

    with pytest.raises(FigureTokenError, match="absent from the drafts"):
        _ = place_figures(version)

    assert drafts.read_bytes() == before


def test_a_section_without_claims_takes_its_figures_as_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _plan(version, [("fig-s0-01", "0"), ("fig-s1-01", "1")])
    path = version / "out" / "drafts.json"
    authored = [{"text": "기존 주장", "source_ids": ["u-1"]}]
    sections = [
        {"section_id": "0", "body": "본문이다.", "claims": []},
        {"section_id": "1", "body": "본문이다.", "claims": authored},
    ]
    _ = path.write_text(json.dumps({"sections": sections}), encoding="utf-8")

    _ = place_figures(version)

    document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    placed = cast(list[dict[str, object]], document["sections"])
    assert placed[0]["claims"] == [{"text": "caption", "source_ids": ["public:c"]}]
    assert placed[1]["claims"] == authored


def test_tokens_close_a_sentence_and_never_sit_on_a_heading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _plan(version, [("fig-s4-01", "4")])
    drafts = _drafts(version, {"4": "### 4-1. 활용 방안\n\n성과를 현장에 적용한다. 후속 시험을 이어간다."})

    _ = place_figures(version)

    document = cast(dict[str, object], json.loads(drafts.read_text(encoding="utf-8")))
    body = cast(str, cast(list[dict[str, object]], document["sections"])[0]["body"])
    assert body == "### 4-1. 활용 방안\n\n성과를 현장에 적용한다 ([[FIG:fig-s4-01]]). 후속 시험을 이어간다."


def test_leading_tokens_from_an_older_placement_become_citations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _plan(version, [("fig-s0-01", "0")])
    drafts = _drafts(version, {"0": "[[FIG:fig-s0-01]] 요약문은 과제를 설명한다."})

    _ = place_figures(version)

    document = cast(dict[str, object], json.loads(drafts.read_text(encoding="utf-8")))
    body = cast(str, cast(list[dict[str, object]], document["sections"])[0]["body"])
    assert body == "요약문은 과제를 설명한다 ([[FIG:fig-s0-01]])."


def test_a_numbered_form_heading_keeps_its_number_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _plan(version, [("fig-s1-01", "1"), ("fig-s2-01", "2")])
    drafts = _drafts(
        version,
        {
            "1": "1-1. 기술적 배경 및 국내외 동향\n실차 굴착은 여러 원인이 겹친다. 둘째 문장이다.",
            "2": "2-1. 최종 목표\n\n원인을 분리해 진단한다.",
        },
    )

    _ = place_figures(version)

    document = cast(dict[str, object], json.loads(drafts.read_text(encoding="utf-8")))
    bodies = [cast(str, s["body"]) for s in cast(list[dict[str, object]], document["sections"])]
    assert bodies[0] == (
        "1-1. 기술적 배경 및 국내외 동향\n실차 굴착은 여러 원인이 겹친다 ([[FIG:fig-s1-01]]). 둘째 문장이다."
    )
    assert bodies[1] == "2-1. 최종 목표\n\n원인을 분리해 진단한다 ([[FIG:fig-s2-01]])."


def test_a_citation_misplaced_by_an_older_rule_is_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _plan(version, [("fig-s1-01", "1")])
    drafts = _drafts(version, {"1": "1-1 ([[FIG:fig-s1-01]]). 기술적 배경\n실차 굴착은 여러 원인이 겹친다."})

    _ = place_figures(version)

    document = cast(dict[str, object], json.loads(drafts.read_text(encoding="utf-8")))
    body = cast(str, cast(list[dict[str, object]], document["sections"])[0]["body"])
    assert body == "1-1. 기술적 배경\n실차 굴착은 여러 원인이 겹친다 ([[FIG:fig-s1-01]])."


def test_the_engine_title_marker_goes_and_no_citation_lands_on_a_heading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _plan(version, [("fig-s3-01", "3")])
    path = version / "out" / "drafts.json"
    section = {
        "section_id": "3",
        "title": "연구내용·방법",
        "body": "연구내용·방법\n### 3-1. 세부 연구 내용 (연차별 / Work Package별)\n\n실차 시험을 수행한다.",
    }
    _ = path.write_text(json.dumps({"sections": [section]}), encoding="utf-8")

    _ = place_figures(version)

    document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    body = cast(str, cast(list[dict[str, object]], document["sections"])[0]["body"])
    assert body == (
        "### 3-1. 세부 연구 내용 (연차별 / Work Package별)\n\n실차 시험을 수행한다 ([[FIG:fig-s3-01]])."
    )
