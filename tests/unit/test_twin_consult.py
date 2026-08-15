from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "wiki" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

wiki_store = import_module("wiki_store")
twin_consult = import_module("twin_consult")

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _write_note(
    root: Path,
    slug: str,
    *,
    tags: list[str],
    kind: str,
    authority: str = "default",
    provenance: str = "stated",
    status: str | None = None,
    updated: str = "2026-07-20T00:00:00Z",
    review_after: str | None = None,
) -> None:
    meta = {
        "title": slug,
        "tags": tags,
        "created": "2026-07-01T00:00:00Z",
        "updated": updated,
        "links": [],
        "kind": kind,
    }
    if kind != "note":
        meta["authority"] = authority
        meta["provenance"] = provenance
    if status is not None:
        meta["status"] = status
    if review_after is not None:
        meta["review_after"] = review_after
    (root / f"{slug}.md").write_text(wiki_store.compose_note(meta, "body"), encoding="utf-8")


def _slugs(result: dict) -> list[str]:
    return [rule["slug"] for rule in result["rules"]]


def test_consult_includes_only_active_judgment_kinds(tmp_path: Path) -> None:
    # Given
    _write_note(tmp_path, "active", tags=["focus"], kind="decision", status="active")
    _write_note(tmp_path, "implicit-active", tags=["focus"], kind="principle")
    _write_note(tmp_path, "plain-note", tags=["focus"], kind="note", status="active")
    _write_note(tmp_path, "superseded", tags=["focus"], kind="decision", status="superseded")
    _write_note(tmp_path, "archived", tags=["focus"], kind="decision", status="archived")

    # When
    result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert set(_slugs(result)) == {"active", "implicit-active"}
    assert {rule["status"] for rule in result["rules"]} == {"active"}


def test_consult_filters_by_shared_tag(tmp_path: Path) -> None:
    # Given
    _write_note(tmp_path, "match", tags=["focus", "lab"], kind="decision")
    _write_note(tmp_path, "miss", tags=["home"], kind="decision")

    # When
    result = twin_consult.consult(tmp_path, ["lab"], None, NOW)

    # Then
    assert _slugs(result) == ["match"]


def test_consult_filters_by_requested_kinds(tmp_path: Path) -> None:
    # Given
    _write_note(tmp_path, "decision", tags=["focus"], kind="decision")
    _write_note(tmp_path, "principle", tags=["focus"], kind="principle")

    # When
    result = twin_consult.consult(tmp_path, ["focus"], ["principle"], NOW)

    # Then
    assert _slugs(result) == ["principle"]


@pytest.mark.parametrize(
    ("winner", "loser", "winner_provenance", "loser_provenance"),
    [
        ("stated", "observed", "stated", "observed"),
        ("observed", "inferred", "observed", "inferred"),
    ],
)
def test_consult_ranks_provenance_before_authority_and_updated(
    tmp_path: Path,
    winner: str,
    loser: str,
    winner_provenance: str,
    loser_provenance: str,
) -> None:
    # Given
    _write_note(
        tmp_path,
        winner,
        tags=["focus"],
        kind="decision",
        authority="advisory",
        provenance=winner_provenance,
        updated="2026-07-01T00:00:00Z",
    )
    _write_note(
        tmp_path,
        loser,
        tags=["focus"],
        kind="decision",
        authority="strict",
        provenance=loser_provenance,
        updated="2026-07-20T00:00:00Z",
    )

    # When
    result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert _slugs(result)[0] == winner


@pytest.mark.parametrize(
    ("winner", "loser", "winner_authority", "loser_authority"),
    [
        ("strict", "default", "strict", "default"),
        ("default", "advisory", "default", "advisory"),
    ],
)
def test_consult_ranks_authority_before_updated(
    tmp_path: Path,
    winner: str,
    loser: str,
    winner_authority: str,
    loser_authority: str,
) -> None:
    # Given
    _write_note(
        tmp_path,
        winner,
        tags=["focus"],
        kind="decision",
        authority=winner_authority,
        updated="2026-07-01T00:00:00Z",
    )
    _write_note(
        tmp_path,
        loser,
        tags=["focus"],
        kind="decision",
        authority=loser_authority,
        updated="2026-07-20T00:00:00Z",
    )

    # When
    result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert _slugs(result)[0] == winner


def test_consult_ranks_updated_descending_after_equal_head_keys(tmp_path: Path) -> None:
    # Given
    _write_note(tmp_path, "older", tags=["focus"], kind="decision", updated="2026-07-01T00:00:00Z")
    _write_note(tmp_path, "newer", tags=["focus"], kind="decision", updated="2026-07-20T00:00:00Z")

    # When
    result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert _slugs(result) == ["newer", "older"]


def test_consult_demotes_expired_authority_before_ranking_and_flips_winner(tmp_path: Path) -> None:
    # Given
    _write_note(
        tmp_path,
        "expired-strict",
        tags=["focus"],
        kind="decision",
        authority="strict",
        updated="2026-07-19T00:00:00Z",
        review_after="2026-07-20",
    )
    _write_note(
        tmp_path,
        "fresh-default",
        tags=["focus"],
        kind="decision",
        authority="default",
        updated="2026-07-20T00:00:00Z",
        review_after="2026-07-22",
    )

    # When
    result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert _slugs(result) == ["fresh-default", "expired-strict"]
    expired = result["rules"][1]
    assert expired["authority"] == "default"
    assert expired["authority_declared"] == "strict"
    assert expired["expired"] is True


def test_consult_demotes_expired_default_to_advisory(tmp_path: Path) -> None:
    # Given
    _write_note(
        tmp_path,
        "expired-default",
        tags=["focus"],
        kind="decision",
        authority="default",
        review_after="2026-07-20",
    )

    # When
    result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert result["rules"][0]["authority"] == "advisory"
    assert result["rules"][0]["expired"] is True


def test_consult_keeps_expired_advisory_at_advisory(tmp_path: Path) -> None:
    # Given
    _write_note(
        tmp_path,
        "expired-advisory",
        tags=["focus"],
        kind="decision",
        authority="advisory",
        review_after="2026-07-20",
    )

    # When
    result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert result["rules"][0]["authority"] == "advisory"
    assert result["rules"][0]["expired"] is True


def test_consult_does_not_expire_review_after_on_current_date(tmp_path: Path) -> None:
    # Given
    _write_note(
        tmp_path,
        "today",
        tags=["focus"],
        kind="decision",
        authority="strict",
        review_after="2026-07-21",
    )

    # When
    result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert result["rules"][0]["authority"] == "strict"
    assert result["rules"][0]["expired"] is False


def test_consult_marks_conflict_for_top_two_same_tag_and_kind_with_different_head(tmp_path: Path) -> None:
    # Given
    _write_note(tmp_path, "strict-rule", tags=["focus"], kind="decision", authority="strict")
    _write_note(tmp_path, "default-rule", tags=["focus"], kind="decision", authority="default")

    # When
    result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert result["verdict"] == "conflict"


def test_consult_returns_none_verdict_for_empty_or_unmatched_vault(tmp_path: Path) -> None:
    # Given
    _write_note(tmp_path, "other", tags=["home"], kind="decision")

    # When
    empty_result = twin_consult.consult(tmp_path / "missing", ["focus"], None, NOW)
    unmatched_result = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert empty_result == {"rules": [], "verdict": "none"}
    assert unmatched_result == {"rules": [], "verdict": "none"}


def test_consult_tie_breaks_by_slug_and_is_deterministic(tmp_path: Path) -> None:
    # Given
    _write_note(tmp_path, "b-rule", tags=["focus"], kind="decision")
    _write_note(tmp_path, "a-rule", tags=["focus"], kind="decision")

    # When
    first = twin_consult.consult(tmp_path, ["focus"], None, NOW)
    second = twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    assert _slugs(first) == ["a-rule", "b-rule"]
    assert first == second


def test_consult_is_read_only_and_leaves_vault_file_hashes_unchanged(tmp_path: Path) -> None:
    # Given
    _write_note(tmp_path, "rule", tags=["focus"], kind="decision")
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.glob("*.md")}

    # When
    twin_consult.consult(tmp_path, ["focus"], None, NOW)

    # Then
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.glob("*.md")}
    assert after == before


def test_cli_prints_json_for_tag_and_kind_filters(tmp_path: Path) -> None:
    # Given
    _write_note(tmp_path, "principle", tags=["focus"], kind="principle")
    _write_note(tmp_path, "decision", tags=["focus"], kind="decision")

    # When
    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPTS / "twin_consult.py"),
            "--root",
            str(tmp_path),
            "--tags",
            "focus",
            "--kinds",
            "principle",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Then
    payload = json.loads(completed.stdout)
    assert _slugs(payload) == ["principle"]
    assert payload["verdict"] in {"ok", "conflict"}
