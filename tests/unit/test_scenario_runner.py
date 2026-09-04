from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation import peer_attest, skill_review


def test_skill_review_and_peer_attest_supply_byte_identical_scenario_environments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one staged skill and a subprocess seam that records each independent run.
    skill_dir = tmp_path / "demo"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    _ = (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: deterministic scenario fixture\n---\n",
        encoding="utf-8",
    )
    _ = (scripts / "scenario.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    captured: list[dict[str, str]] = []

    def capture_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        captured.append({str(key): str(value) for key, value in environment.items()})
        return subprocess.CompletedProcess(("bash",), 0, "SCENARIO-PASS\n", "")

    monkeypatch.setattr(subprocess, "run", capture_run)
    monkeypatch.setenv("INTEROP_RUNTIME", str(tmp_path / "interop-runtime"))
    digest = skill_review.skill_digest(skill_dir)
    request = peer_attest.AttestRequest(
        "demo",
        skill_dir,
        digest,
        "message-1",
        "1" * 32,
        "channel-1",
    )

    # When: skill_review and peer_attest each enter their own public review path.
    assert skill_review._scenario_passes(skill_dir, None)
    assert peer_attest._review_attempt(request, None) is not None

    # Then: both calls carry the same complete deployment scenario contract.
    assert len(captured) == 2
    comparable = [{key: value for key, value in env.items() if key != "HOME"} for env in captured]
    assert comparable[0] == comparable[1]
    assert set(comparable[0]) == {
        "AUTOPHAGY_DEMO_SECRET",
        "AUTOPHAGY_REPO_ROOT",
        "AUTOPHAGY_SKILL_LIVE_ROOT",
        "INTEROP_RUNTIME",
        "PATH",
    }
    assert comparable[0]["INTEROP_RUNTIME"] == str(tmp_path / "interop-runtime")
    # The staged skill is a copy; declaring its parent as the live root is what lets the
    # governed-copy guard accept the review run instead of refusing a "stale copy".
    assert comparable[0]["AUTOPHAGY_SKILL_LIVE_ROOT"] == str(skill_dir.resolve().parent)
