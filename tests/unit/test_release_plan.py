"""VA-1 release plan: changed deployment surfaces and bounded patch-note rendering."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from automation.release_plan import ReleasePlanError, build_plan, render_patch_notes
from automation.skill_review import skill_digest


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "release-test",
            "GIT_AUTHOR_EMAIL": "release@example.invalid",
            "GIT_COMMITTER_NAME": "release-test",
            "GIT_COMMITTER_EMAIL": "release@example.invalid",
        },
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "skills" / "demo").mkdir(parents=True)
    _ = (repo / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: deterministic demo skill\n---\n",
        encoding="utf-8",
    )
    (repo / "automation" / "pkg").mkdir(parents=True)
    _ = (repo / "automation" / "pkg" / "watch.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (repo / "automation" / "interop").mkdir()
    _ = (repo / "automation" / "interop" / "approval_surface.py").write_text(
        "POLICY_VERSION: Final = 8\n", encoding="utf-8"
    )
    (repo / "configs").mkdir()
    _ = (repo / "configs" / "watcher-deploy-manifest.txt").write_text(
        "agent|automation/pkg/watch.py|.hermes/scripts/watch.py|required\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo


def test_plan_uses_real_skill_digest_and_manifest_package_owner(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _ = (repo / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: changed deterministic demo skill\n---\n",
        encoding="utf-8",
    )
    _ = (repo / "automation" / "pkg" / "watch.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    _git(repo, "commit", "-am", "change demo and wrapper")
    head = _git(repo, "rev-parse", "HEAD")

    plan = build_plan(repo, base=base, head=head, version="v1.2.3")
    surfaces = dict(plan.surface_digests)

    assert surfaces["skill:demo"] == skill_digest(repo / "skills" / "demo")
    assert len(surfaces["home:automation/pkg"]) == 64
    assert plan.changed_paths == (
        "automation/pkg/watch.py",
        "skills/demo/SKILL.md",
    )
    assert plan.commit_titles == ("change demo and wrapper",)


def test_non_deployment_changes_are_still_bound_to_the_release_tree(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "docs").mkdir()
    _ = (repo / "docs" / "guide.md").write_text("public docs\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add docs")
    head = _git(repo, "rev-parse", "HEAD")

    plan = build_plan(repo, base=base, head=head, version="v1.2.3")

    assert ("repo", plan.tree_digest) in plan.surface_digests
    surfaces = dict(plan.surface_digests)
    assert surfaces["skill:demo"] == skill_digest(repo / "skills" / "demo")
    assert len(surfaces["home:automation/pkg"]) == 64


def test_patch_notes_cap_commit_titles_at_twenty_lines(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    for index in range(22):
        _git(repo, "commit", "--allow-empty", "-m", f"change {index:02d}")
    head = _git(repo, "rev-parse", "HEAD")
    plan = build_plan(repo, base=base, head=head, version="v1.2.3")

    rendered = render_patch_notes(plan)

    assert rendered.count("\n  - change ") == 20
    assert "  - +2건" in rendered
    assert "change 20" not in rendered
    assert "change 21" not in rendered


def test_policy_version_change_refuses_non_major_bump(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    policy = repo / "automation" / "interop" / "approval_surface.py"
    _ = policy.write_text("POLICY_VERSION: Final = 9\n", encoding="utf-8")
    _git(repo, "commit", "-am", "change approval policy")
    head = _git(repo, "rev-parse", "HEAD")

    try:
        build_plan(repo, base=base, head=head, version="v1.0.1", bump="patch")
    except ReleasePlanError as error:
        assert "POLICY_VERSION" in str(error)
    else:
        raise AssertionError("POLICY_VERSION change must require a major release")


def test_policy_version_change_adds_major_operator_note(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    policy = repo / "automation" / "interop" / "approval_surface.py"
    _ = policy.write_text("POLICY_VERSION: Final = 9\n", encoding="utf-8")
    _git(repo, "commit", "-am", "change approval policy")
    head = _git(repo, "rev-parse", "HEAD")

    plan = build_plan(repo, base=base, head=head, version="v2.0.0", bump="major")

    notes = render_patch_notes(plan)
    assert notes.startswith("MAJOR: 운영자 조치 필요 — ")
    assert "POLICY_VERSION" in notes.splitlines()[0]


def test_patch_bump_allows_a_range_without_major_signals(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _ = (repo / "automation" / "pkg" / "watch.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    _git(repo, "commit", "-am", "ordinary runtime change")
    head = _git(repo, "rev-parse", "HEAD")

    plan = build_plan(repo, base=base, head=head, version="v1.0.1", bump="patch")

    assert "MAJOR:" not in render_patch_notes(plan)


def test_plan_is_deterministic_for_the_same_git_range(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-m", "same input")
    head = _git(repo, "rev-parse", "HEAD")

    first = build_plan(repo, base=base, head=head, version="v1.2.3")
    second = build_plan(repo, base=base, head=head, version="v1.2.3")

    assert first == second
    assert render_patch_notes(first) == render_patch_notes(second)
