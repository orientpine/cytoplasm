from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from automation.managed_sync.fetch import sync_remote


@dataclass(frozen=True, slots=True)
class _Config:
    remote_url: str
    mirror_dir: Path
    ssh_key_path: Path


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _tag_only_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _ = _git("init", "--bare", str(remote))
    _ = _git("init", str(work))
    _ = _git("config", "user.email", "fixture@example.invalid", cwd=work)
    _ = _git("config", "user.name", "Fixture", cwd=work)
    _ = (work / "skill.txt").write_text("release\n", encoding="utf-8")
    _ = _git("add", "skill.txt", cwd=work)
    _ = _git("commit", "-m", "fixture", cwd=work)
    _ = _git("tag", "managed-lab/v1", cwd=work)
    _ = _git("push", str(remote), "refs/tags/managed-lab/v1", cwd=work)
    return remote


def test_skill_sync_when_roster_branch_is_absent_then_tag_delivery_still_succeeds(
    tmp_path: Path,
) -> None:
    # Given: a valid managed-skill feed that has a release tag but no roster branch.
    remote = _tag_only_remote(tmp_path)
    config = _Config(
        remote_url=str(remote),
        mirror_dir=tmp_path / "mirror",
        ssh_key_path=tmp_path / "unused-local-key",
    )

    # When: the ordinary skill transport refreshes its existing mirror.
    result = sync_remote(config)

    # Then: missing optional roster transport cannot stop tag delivery.
    assert result.cloned is True
    tags = _git("-C", str(config.mirror_dir), "tag", "--list", "managed-lab/v1")
    assert tags.stdout.strip() == "managed-lab/v1"
