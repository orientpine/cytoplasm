"""The preview subprocess must learn the node's browser from the skill environment.

Split from test_proposal_visual_review.py: that file pins the review workflow, this one pins
the browser hand-off that only failed on the node (2026-08-31: KIMM_DOCBOT_CHROME was set in
~/.env.secrets but the child env whitelist dropped it and the engine found no browser).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from skills.proposal.scripts import proposal_visual_review
from skills.proposal.scripts.proposal_config import PreflightReport, ProposalConfig
from skills.proposal.scripts.proposal_version import Staging, VersionStore


def _config(tmp_path: Path) -> ProposalConfig:
    docbot = tmp_path / "docbot"
    docbot.mkdir()
    return ProposalConfig(
        docbot_root=docbot,
        docbot_pin="a" * 40,
        profile="10-page",
        image_model="gpt-image-2",
        image_monthly_cap_usd=10,
        refine_pin="b" * 40,
        drive_root="outputs",
        state_root=tmp_path / "state",
    )


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProposalConfig:
    root = tmp_path / "proposals"
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(str(root).encode()).hexdigest())
    assert isinstance(staging, Staging)
    version = store.promote("demo", staging, {"parent": None, "schema_version": 1})
    (root / "demo" / "versions" / version / "out" / "proposal.hwpx").write_bytes(b"proposal")
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    config = _config(tmp_path)
    monkeypatch.setattr(
        proposal_visual_review,
        "preflight",
        lambda _config: PreflightReport(
            config.docbot_pin, config.docbot_pin, True, "c" * 64, True, ()
        ),
    )
    return config


def _runner(calls: list[tuple[list[str], dict[str, Any]]]):
    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        out = Path(argv[argv.index("--out-dir") + 1])
        (out / "pages").mkdir(parents=True)
        (out / "preview.html").write_text("<html></html>", encoding="utf-8")
        (out / "preview.pdf").write_bytes(b"%PDF")
        (out / "pages" / "page-01.png").write_bytes(b"png")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return runner


def test_configured_preview_browser_reaches_the_engine_by_flag_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("PROPOSAL_PREVIEW_CHROME", "/opt/browsers/headless_shell")
    monkeypatch.setenv("KIMM_DOCBOT_CHROME", "/opt/browsers/headless_shell")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    proposal_visual_review.run_visual_review("demo", config=config, runner=_runner(calls))

    argv, kwargs = calls[0]
    assert argv[argv.index("--chrome") + 1] == "/opt/browsers/headless_shell"
    assert kwargs["env"]["KIMM_DOCBOT_CHROME"] == "/opt/browsers/headless_shell"


def test_unconfigured_preview_browser_leaves_resolution_to_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _prepare(tmp_path, monkeypatch)
    monkeypatch.delenv("PROPOSAL_PREVIEW_CHROME", raising=False)
    monkeypatch.delenv("KIMM_DOCBOT_CHROME", raising=False)
    calls: list[tuple[list[str], dict[str, Any]]] = []

    proposal_visual_review.run_visual_review("demo", config=config, runner=_runner(calls))

    argv, kwargs = calls[0]
    assert "--chrome" not in argv
    assert "KIMM_DOCBOT_CHROME" not in kwargs["env"]
