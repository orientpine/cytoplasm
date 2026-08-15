"""DT-B4: wiki CLI twin authoring flags + per-kind body-template soft warnings.

The draft subcommand accepts --kind/--authority/--provenance/--status/
--review-after/--supersedes; invalid values hit the existing SchemaError path
(exit 2 SCHEMA-REJECTED). Missing template headings only WARN, never block.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))

import wiki_cli  # noqa: E402
import wiki_gate  # noqa: E402
import wiki_store  # noqa: E402

DECISION_BODY = (
    "## Context\n배경\n"
    "## Decision\n결정\n"
    "## Rationale & Trade-offs\n근거\n"
    "## What would change my mind\n조건\n"
)


@pytest.fixture()
def wiki_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    monkeypatch.setenv("WIKI_ROOT", str(root))
    monkeypatch.setenv("WIKI_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setattr(wiki_cli, "WIKI_ROOT", root)
    monkeypatch.setattr(wiki_gate, "GATE_DIR", tmp_path / "gate")
    return root


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["wiki", *argv])
    return wiki_cli.main()


def _draft_meta(stdout: str) -> dict:
    match = re.search(r"DRAFT-CREATED id=(\w+)", stdout)
    assert match, stdout
    record = wiki_gate.load_draft(match.group(1))
    meta, _ = wiki_store.parse_note(record["note_text"])
    return meta


def _write_note(root: Path, slug: str, meta: dict, body: str) -> None:
    (root / f"{slug}.md").write_text(wiki_store.compose_note(meta, body), encoding="utf-8")


def _decision_meta(**overrides: str) -> dict:
    meta = {
        "title": "Twin Decision",
        "tags": ["연구"],
        "created": "2026-07-01T00:00:00Z",
        "updated": "2026-07-01T00:00:00Z",
        "links": [],
        "kind": "decision",
        "authority": "strict",
        "provenance": "stated",
        "review_after": "2026-10-01",
    }
    meta.update(overrides)
    return meta


def test_draft_with_twin_flags_creates_note_with_twin_meta_and_no_warn(
    wiki_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a decision draft request whose body carries all 4 template headings

    # When
    rc = _run(
        monkeypatch,
        "draft",
        "--title", "Twin Decision",
        "--kind", "decision",
        "--authority", "default",
        "--provenance", "stated",
        "--review-after", "2026-12-01",
        "--body", DECISION_BODY,
    )

    # Then
    captured = capsys.readouterr()
    assert rc == 0
    meta = _draft_meta(captured.out)
    assert meta["kind"] == "decision"
    assert meta["authority"] == "default"
    assert meta["provenance"] == "stated"
    assert meta["review_after"] == "2026-12-01"
    assert "TEMPLATE-WARN" not in captured.out


def test_draft_with_status_and_supersedes_flags_lands_in_meta(
    wiki_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a note-kind draft using the remaining twin flags

    # When
    rc = _run(
        monkeypatch,
        "draft",
        "--title", "Twin Note",
        "--kind", "note",
        "--status", "superseded",
        "--supersedes", "old-note",
        "--body", "본문\n",
    )

    # Then
    meta = _draft_meta(capsys.readouterr().out)
    assert rc == 0
    assert meta["kind"] == "note"
    assert meta["status"] == "superseded"
    assert meta["supersedes"] == "old-note"


@pytest.mark.parametrize(
    "argv",
    [
        ("--kind", "decision", "--authority", "binding", "--provenance", "stated"),
        ("--kind", "rule"),
        ("--kind", "note", "--review-after", "2026/12/01"),
    ],
)
def test_draft_rejects_invalid_twin_values_with_schema_exit_2(
    wiki_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
) -> None:
    # Given: an invalid enum value or malformed review-after date

    # When
    rc = _run(monkeypatch, "draft", "--title", "Bad Twin", "--body", "본문\n", *argv)

    # Then
    captured = capsys.readouterr()
    assert rc == 2
    assert "SCHEMA-REJECTED" in captured.err
    assert "DRAFT-CREATED" not in captured.out


@pytest.mark.parametrize(
    ("kind", "extra", "body", "missing"),
    [
        (
            "decision",
            ("--authority", "default", "--provenance", "stated"),
            "## Context\n배경\n## Rationale & Trade-offs\n근거\n## What would change my mind\n조건\n",
            ["## Decision"],
        ),
        (
            "principle",
            ("--authority", "default", "--provenance", "stated"),
            "## Trigger\n계기\n## Rule\n규칙\n",
            ["## Exceptions"],
        ),
        (
            "preference",
            ("--authority", "advisory", "--provenance", "observed"),
            "선호만 서술\n",
            ["## Preference", "## Boundary"],
        ),
    ],
)
def test_draft_with_missing_headings_still_created_but_warns(
    wiki_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    extra: tuple[str, ...],
    body: str,
    missing: list[str],
) -> None:
    # Given: a judgment-kind draft whose body lacks template headings

    # When
    rc = _run(
        monkeypatch,
        "draft", "--title", f"Warn {kind}", "--kind", kind, "--body", body, *extra,
    )

    # Then: draft is STILL created (guided, not enforced) and stdout warns
    captured = capsys.readouterr()
    assert rc == 0
    assert "DRAFT-CREATED" in captured.out
    assert _draft_meta(captured.out)["kind"] == kind
    warn_lines = [line for line in captured.out.splitlines() if line.startswith("TEMPLATE-WARN")]
    assert len(warn_lines) == len(missing)
    for heading in missing:
        assert any(heading in line for line in warn_lines)


def test_edit_preserves_existing_twin_keys_without_flags(
    wiki_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an existing decision note with twin keys
    _write_note(wiki_root, "twin-decision", _decision_meta(), DECISION_BODY)

    # When: editing without any twin flag
    rc = _run(monkeypatch, "draft", "--edit", "twin-decision", "--body", DECISION_BODY)

    # Then: every existing twin key survives
    meta = _draft_meta(capsys.readouterr().out)
    assert rc == 0
    assert meta["kind"] == "decision"
    assert meta["authority"] == "strict"
    assert meta["provenance"] == "stated"
    assert meta["review_after"] == "2026-10-01"


def test_edit_overrides_only_flagged_twin_keys(
    wiki_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an existing decision note with authority=strict
    _write_note(wiki_root, "twin-decision", _decision_meta(), DECISION_BODY)

    # When: overriding a single twin key
    rc = _run(
        monkeypatch,
        "draft", "--edit", "twin-decision", "--authority", "advisory",
    )

    # Then: the flagged key changes, the rest are preserved
    meta = _draft_meta(capsys.readouterr().out)
    assert rc == 0
    assert meta["authority"] == "advisory"
    assert meta["kind"] == "decision"
    assert meta["provenance"] == "stated"
    assert meta["review_after"] == "2026-10-01"


def test_template_warnings_names_each_missing_heading() -> None:
    # Given: a decision body containing only the Context heading
    body = "## Context\n배경\n"

    # When
    warnings = wiki_cli.template_warnings("decision", body)

    # Then
    assert len(warnings) == 3
    joined = "\n".join(warnings)
    for heading in ("## Decision", "## Rationale & Trade-offs", "## What would change my mind"):
        assert heading in joined
    assert all(line.startswith("TEMPLATE-WARN") for line in warnings)


def test_template_warnings_empty_for_note_kind_and_complete_bodies() -> None:
    # Given: a kind with no template plus fully templated bodies

    # When / Then
    assert wiki_cli.template_warnings("note", "아무 본문\n") == []
    assert wiki_cli.template_warnings("decision", DECISION_BODY) == []
    assert (
        wiki_cli.template_warnings("principle", "## Trigger\nt\n## Rule\nr\n## Exceptions\ne\n")
        == []
    )
    assert wiki_cli.template_warnings("preference", "## Preference\np\n## Boundary\nb\n") == []
