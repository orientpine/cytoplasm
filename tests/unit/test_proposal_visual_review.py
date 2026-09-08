from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from skills.proposal.scripts import proposal_visual_review
from skills.proposal.scripts.proposal_config import ProposalConfig
from skills.proposal.scripts.proposal_version import Staging, VersionStore


def _config(tmp_path: Path) -> ProposalConfig:
    return ProposalConfig(
        profile="10-page",
        image_model="gpt-image-2",
        image_monthly_cap_usd=10,
        refine_pin="b" * 40,
        drive_root="outputs",
        state_root=tmp_path / "state",
    )


def _version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proposals"
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(str(root).encode()).hexdigest())
    assert isinstance(staging, Staging)
    version = store.promote("demo", staging, {"parent": None, "schema_version": 1})
    version_path = root / "demo" / "versions" / version
    _ = (version_path / "out" / "proposal.hwpx").write_bytes(b"proposal")
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    return version_path


def test_visual_review_calls_the_in_tree_engine_and_returns_page_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_path = _version(tmp_path, monkeypatch)
    config = _config(tmp_path)
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> int:
        calls.append(argv)
        out = Path(argv[argv.index("--out-dir") + 1])
        (out / "pages").mkdir(parents=True)
        _ = (out / "preview.html").write_text("<html></html>", encoding="utf-8")
        _ = (out / "preview.pdf").write_bytes(b"%PDF")
        _ = (out / "pages" / "page-01.png").write_bytes(b"png")
        return 0

    result = proposal_visual_review.run_visual_review("demo", config=config, runner=runner)

    digest = hashlib.sha256(b"proposal").hexdigest()
    assert result.version == version_path.name
    assert result.hwpx_sha256 == digest
    assert [path.name for path in result.page_paths] == ["page-01.png"]
    assert result.output_dir == (
        config.state_root / "visual-reviews" / "demo" / version_path.name / digest
    )
    assert calls[0][0] == "preview"


def test_visual_review_launches_no_external_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine is a module now; no launcher token may reach the seam."""
    _ = _version(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> int:
        calls.append(argv)
        out = Path(argv[argv.index("--out-dir") + 1])
        (out / "pages").mkdir(parents=True)
        _ = (out / "preview.html").write_text("<html></html>", encoding="utf-8")
        _ = (out / "preview.pdf").write_bytes(b"%PDF")
        _ = (out / "pages" / "page-01.png").write_bytes(b"png")
        return 0

    _ = proposal_visual_review.run_visual_review(
        "demo", config=_config(tmp_path), runner=runner
    )

    assert not {"uv", "run", "kimm-docbot"} & set(calls[0]), calls[0]


def test_visual_review_reuses_complete_digest_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_path = _version(tmp_path, monkeypatch)
    config = _config(tmp_path)
    digest = hashlib.sha256(b"proposal").hexdigest()
    out = config.state_root / "visual-reviews" / "demo" / version_path.name / digest
    (out / "pages").mkdir(parents=True)
    _ = (out / "preview.html").write_text("<html></html>", encoding="utf-8")
    _ = (out / "preview.pdf").write_bytes(b"%PDF")
    _ = (out / "pages" / "page-01.png").write_bytes(b"png")

    def refusing_runner(_argv: list[str]) -> int:
        raise AssertionError("the engine should not run for a complete digest directory")

    result = proposal_visual_review.run_visual_review(
        "demo", config=config, runner=refusing_runner
    )

    assert result.reused is True


def test_engine_failure_is_reported_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _version(tmp_path, monkeypatch)

    with pytest.raises(proposal_visual_review.VisualReviewError, match="rc=3"):
        _ = proposal_visual_review.run_visual_review(
            "demo", config=_config(tmp_path), runner=lambda _argv: 3
        )


def test_visual_review_command_emits_machine_readable_page_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = tmp_path / "page-01.png"
    _ = page.write_bytes(b"png")
    result = proposal_visual_review.VisualReviewResult(
        "demo",
        "v000010",
        "a" * 64,
        tmp_path,
        tmp_path / "preview.html",
        tmp_path / "preview.pdf",
        (page,),
        False,
    )
    monkeypatch.setattr(proposal_visual_review, "run_visual_review", lambda _slug: result)

    assert proposal_visual_review.command(
        type("Args", (), {"slug": "demo", "json": True})()
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pages"] == [str(page)]
