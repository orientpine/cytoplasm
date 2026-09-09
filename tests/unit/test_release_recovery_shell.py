"""Exercise both real shell entry points without Git mutations or network traffic."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_release_approval import _pending

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    # Given: an approved old card, an advanced clean tip, isolated state and fake Git.
    gate = tmp_path / "gate"
    record = _pending(gate)
    (gate / "seed.json").write_text(json.dumps(record), encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    git = binaries / "git"
    git.write_text('''#!/usr/bin/env bash
case "$*" in
  *"rev-parse"*) printf '%s\\n' "$TIP" ;;
  *"rev-list"*) printf '%s\\n' "$BASE" ;;
  *) exit 0 ;;
esac
''', encoding="utf-8")
    git.chmod(0o755)
    library = tmp_path / "tag-lib.sh"
    library.write_text('''release_version_for() { printf 'v1.2.4\\n'; }
latest_release_base() { printf '%s\\n' "$BASE"; }
ensure_signed_tag() { printf '%s\\n' "$2" >> "$GATE_DIR/tags"; }
''', encoding="utf-8")
    ci = tmp_path / "ci.sh"
    ci.write_text("exit 0\n", encoding="utf-8")
    return {
        **os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
        "GATE_DIR": str(gate), "TIP": "e" * 40, "BASE": "c" * 40,
        "RELEASE_REPO_ROOT": str(worktree), "RELEASE_TAG_LIB": str(library),
        "RELEASE_LOCAL_CI": str(ci), "RELEASE_DEADLINE_SECONDS": "0",
        "RELEASE_APPROVAL_CMD": f"{sys.executable} {_ROOT / 'tests/unit/release_recovery_driver.py'}",
        "RELEASE_COMPLETE_WORKTREE": str(worktree),
        "RELEASE_COMPLETE_STATE": str(tmp_path / "state"),
        "NEW_PROBE": "bound-pending",
    }


@pytest.mark.parametrize("new_probe,expected_rc", [("bound-pending", 8), ("approved", 0)])
def test_release_posts_once_when_approved_request_was_overtaken(
    environment: dict[str, str], new_probe: str, expected_rc: int,
) -> None:
    # Given: the new owner decision differs from the already-approved old message.
    env = {**environment, "NEW_PROBE": new_probe}
    gate = Path(env["GATE_DIR"])
    # When: the real release entry point runs.
    result = subprocess.run(["bash", str(_ROOT / "automation/release.sh"), "--no-deploy"],
                            env=env, text=True, capture_output=True, timeout=20, check=False)
    # Then: recovery executes before the one new request, and only its approval can tag.
    assert result.returncode == expected_rc, result.stdout + result.stderr
    assert "RELEASE-ABANDONED" in result.stdout
    print(result.stdout + result.stderr)
    calls = (gate / "calls").read_text().splitlines()
    assert calls.count("request") == 1
    assert calls.index("retire") < calls.index("request")
    tags = gate / "tags"
    assert (tags.read_text().splitlines() if tags.exists() else []) == (
        [env["TIP"]] if new_probe == "approved" else []
    )
    assert json.loads((gate / "pending/release.json").read_text())["message_id"] == "new-card"


def test_completer_notifies_once_when_approved_request_is_stale_across_ticks(
    environment: dict[str, str],
) -> None:
    # Given: no request creation or release execution is allowed in this stale episode.
    gate = Path(environment["GATE_DIR"])
    # When: two real completer processes run against different current tips.
    results = [subprocess.run(["bash", str(_ROOT / "automation/release_complete.sh")],
                              env={**environment, "TIP": tip * 40}, text=True,
                              capture_output=True, timeout=20, check=False)
               for tip in ("e", "f")]
    # Then: one delivered notice, unchanged approval bytes, no release/request call.
    assert [r.returncode for r in results] == [0, 0]
    notices = gate / "notices"
    assert (notices.read_text().splitlines() if notices.exists() else []) == ["notice"]
    assert (gate / "calls").read_text().splitlines() == ["decision", "decision"]
    assert not (gate / "tags").exists()
