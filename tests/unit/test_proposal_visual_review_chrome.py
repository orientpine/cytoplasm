"""The preview step must learn the node's browser from the skill environment.

Split from test_proposal_visual_review.py: that file pins the review workflow, this one pins
the browser hand-off that only failed on the node (2026-08-31: KIMM_DOCBOT_CHROME was set in
~/.env.secrets but the subprocess child-env whitelist dropped it and the engine found no
browser). The engine runs in this process now, so no whitelist stands between the two — these
tests pin that the flag still reaches it and that the variable is genuinely visible when it does.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from skills.proposal.scripts import proposal_visual_review
from skills.proposal.scripts.proposal_config import ProposalConfig
from skills.proposal.scripts.proposal_version import Staging, VersionStore

Call = tuple[list[str], str | None]


def _config(tmp_path: Path) -> ProposalConfig:
    return ProposalConfig(
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
    _ = (root / "demo" / "versions" / version / "out" / "proposal.hwpx").write_bytes(
        b"proposal"
    )
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    return _config(tmp_path)


def _runner(calls: list[Call]):
    def runner(argv: list[str]) -> int:
        calls.append((argv, os.environ.get("KIMM_DOCBOT_CHROME")))
        out = Path(argv[argv.index("--out-dir") + 1])
        (out / "pages").mkdir(parents=True)
        _ = (out / "preview.html").write_text("<html></html>", encoding="utf-8")
        _ = (out / "preview.pdf").write_bytes(b"%PDF")
        _ = (out / "pages" / "page-01.png").write_bytes(b"png")
        return 0

    return runner


def test_configured_preview_browser_reaches_the_engine_by_flag_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("PROPOSAL_PREVIEW_CHROME", "/opt/browsers/headless_shell")
    monkeypatch.setenv("KIMM_DOCBOT_CHROME", "/opt/browsers/headless_shell")
    calls: list[Call] = []

    _ = proposal_visual_review.run_visual_review("demo", config=config, runner=_runner(calls))

    argv, seen_browser = calls[0]
    assert argv[argv.index("--chrome") + 1] == "/opt/browsers/headless_shell"
    assert seen_browser == "/opt/browsers/headless_shell"


def test_unconfigured_preview_browser_leaves_resolution_to_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _prepare(tmp_path, monkeypatch)
    monkeypatch.delenv("PROPOSAL_PREVIEW_CHROME", raising=False)
    monkeypatch.delenv("KIMM_DOCBOT_CHROME", raising=False)
    calls: list[Call] = []

    _ = proposal_visual_review.run_visual_review("demo", config=config, runner=_runner(calls))

    argv, seen_browser = calls[0]
    assert "--chrome" not in argv
    assert seen_browser is None
