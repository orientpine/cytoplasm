"""Keep refined-lineage staging coverage separate from FS3-pinned proposal tests."""

from __future__ import annotations

from pathlib import Path

from skills.proposal.scripts.proposal_improve_cmd import (
    RegenerationPlan,
    _copy_reusable_artifacts,
)


def test_staging_seeds_child_drafts_from_parent_refined_lineage(tmp_path: Path) -> None:
    """A refined parent draft is the next child's seed, not its stale source draft."""
    parent = tmp_path / "demo" / "versions" / "v000006"
    child = tmp_path / "demo" / "staging" / "child"
    out = parent / "out"
    out.mkdir(parents=True)
    stale = b'{"figures":["fig-s3-01"]}\n'
    refined = b'{"figures":["fig-s3-01","fig-s3-04"]}\n'
    _ = (out / "drafts.json").write_bytes(stale)
    _ = (out / "drafts.refined.json").write_bytes(refined)

    _copy_reusable_artifacts(
        parent, child, RegenerationPlan(frozenset(), frozenset(), frozenset(), ())
    )

    assert (child / "out" / "drafts.json").read_bytes() == refined


def test_staging_falls_back_to_parent_source_drafts_without_refined_lineage(
    tmp_path: Path,
) -> None:
    """A parent without refinement keeps its source draft as the child seed."""
    parent = tmp_path / "demo" / "versions" / "v000006"
    child = tmp_path / "demo" / "staging" / "child"
    out = parent / "out"
    out.mkdir(parents=True)
    source = b'{"figures":["fig-s3-01"]}\n'
    _ = (out / "drafts.json").write_bytes(source)

    _copy_reusable_artifacts(
        parent, child, RegenerationPlan(frozenset(), frozenset(), frozenset(), ())
    )

    assert (child / "out" / "drafts.json").read_bytes() == source
