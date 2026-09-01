from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from skills.proposal.scripts.proposal_excavator_e2e import _fill_body, augment
from skills.proposal.scripts.proposal_route_guard import assert_route_allowed, classify


def test_augment_writes_private_version_files_under_permissive_umask(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    sections = [{"body": "초기 내용을 검증한다.", "section_id": str(index)} for index in range(5)]
    drafts = out / "drafts.json"
    drafts.write_text(json.dumps({"sections": sections}), encoding="utf-8")
    (out / "drafts.json.pms.json").write_text(
        json.dumps({"ledger": {f"C{index:02d}": "PUBLIC" for index in range(1, 6)}}),
        encoding="utf-8",
    )

    previous_umask = os.umask(0o022)
    try:
        augment(tmp_path)
    finally:
        os.umask(previous_umask)

    written = (drafts, tmp_path / "figures.json", tmp_path / "tables.json")
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in written)

    augmented = json.loads(drafts.read_text(encoding="utf-8"))
    figures = json.loads((tmp_path / "figures.json").read_text(encoding="utf-8"))
    claims_by_section = {
        section["section_id"]: [claim["text"] for claim in section["claims"]]
        for section in augmented["sections"]
    }

    assert len({figure["prompt"] for figure in figures}) == 15
    assert len({figure["caption"] for figure in figures}) == 15
    for figure in figures:
        claim = figure["caption"]
        assert claim in claims_by_section[figure["section_id"]]
        assert claim in figure["prompt"]
        assert figure["prompt"].endswith("no text, no labels, no numerals")


def test_fill_body_preserves_authored_paragraph_structure() -> None:
    body = "## 소제목\n\n첫 문단이다. 이어지는 문장이다.\n\n둘째 문단이다.\n\n셋째 문단이다."

    filled = _fill_body(body, "0", ["fig-s0-01", "fig-s0-02"], 4000)

    assert "## 소제목\n" in filled, f"heading glued to the next block: {filled!r}"
    assert "검증 관점" not in filled, f"canned filler injected: {filled!r}"
    for block in ("## 소제목", "첫 문단이다. 이어지는 문장이다.", "둘째 문단이다.", "셋째 문단이다."):
        assert block in filled, f"block was cut apart: {block!r} missing from {filled!r}"
    assert filled.count("[[FIG:fig-s0-01]]") == 1
    assert filled.count("[[FIG:fig-s0-02]]") == 1


def test_fill_body_never_pads_a_short_body_with_canned_filler() -> None:
    filled = _fill_body("짧은 본문이다.", "0", ["fig-s0-01"], 5000)

    assert "검증 관점" not in filled, f"canned filler injected: {filled!r}"
    assert len(filled) < 200, f"body was padded to {len(filled)} chars"


def test_fill_body_leaves_an_already_authored_body_untouched() -> None:
    body = "[[FIG:fig-s0-01]] 첫 밴드다.\n\n[[FIG:fig-s0-02]] 둘째 밴드다."

    assert _fill_body(body, "0", ["fig-s0-01", "fig-s0-02"], 5000) == body


def test_generated_figure_prompts_are_routable_to_the_image_api(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    sections = [
        {"body": "지형 관측값을 작업 계약으로 연결한다.", "section_id": str(index)} for index in range(5)
    ]
    (out / "drafts.json").write_text(json.dumps({"sections": sections}), encoding="utf-8")
    (out / "drafts.json.pms.json").write_text(
        json.dumps({"ledger": {f"C{index:02d}": "PUBLIC" for index in range(1, 6)}}),
        encoding="utf-8",
    )

    augment(tmp_path)

    figures = json.loads((tmp_path / "figures.json").read_text(encoding="utf-8"))
    for figure in figures:
        assert classify(figure["prompt"]) != "patent-sensitive", (
            f"prompt wording trips the patent gate: {figure['prompt']!r}"
        )
        assert_route_allowed(figure["prompt"], "image-api")


def test_figure_captions_carry_no_rendered_number_prefix(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    sections = [
        {"body": "지형 관측값을 작업 계약으로 연결한다.", "section_id": str(index)} for index in range(5)
    ]
    (out / "drafts.json").write_text(json.dumps({"sections": sections}), encoding="utf-8")
    (out / "drafts.json.pms.json").write_text(
        json.dumps({"ledger": {f"C{index:02d}": "PUBLIC" for index in range(1, 6)}}),
        encoding="utf-8",
    )

    augment(tmp_path)

    figures = json.loads((tmp_path / "figures.json").read_text(encoding="utf-8"))
    for figure in figures:
        assert re.match(r"그림\s*\d+\.", figure["caption"]) is None, (
            "image_embed already prepends '그림 N. '; the manifest must hold the description only, "
            f"got {figure['caption']!r}"
        )
