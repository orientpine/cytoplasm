"""VA-1 release plan: changed deployment surfaces and bounded patch-note rendering."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from automation.release_notes import major_note, render_detail_messages
from automation.release_plan import ReleasePlanError, build_plan
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


def test_a_long_release_keeps_every_commit_in_the_detail_messages(tmp_path: Path) -> None:
    """Given far more commits than one message holds / When the details render /
    Then every commit is still there, once, with no truncation marker."""
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    for index in range(60):
        _git(repo, "commit", "--allow-empty", "-m", f"변경 {index:02d} " + "다" * 60)
    head = _git(repo, "rev-parse", "HEAD")
    plan = build_plan(repo, base=base, head=head, version="v1.2.3")

    joined = "\n".join(render_detail_messages(plan))

    assert len(plan.commits) == 60
    for commit in plan.commits:
        assert joined.count(commit.subject) == 1
    assert "생략" not in joined
    assert "omitted" not in joined
    assert "…" not in joined


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

    note = major_note(plan)
    assert note.startswith("MAJOR: 운영자 조치 필요 — ")
    assert "POLICY_VERSION" in note


def test_patch_bump_allows_a_range_without_major_signals(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _ = (repo / "automation" / "pkg" / "watch.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    _git(repo, "commit", "-am", "ordinary runtime change")
    head = _git(repo, "rev-parse", "HEAD")

    plan = build_plan(repo, base=base, head=head, version="v1.0.1", bump="patch")

    assert major_note(plan) == ""


def test_plan_is_deterministic_for_the_same_git_range(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-m", "same input")
    head = _git(repo, "rev-parse", "HEAD")

    first = build_plan(repo, base=base, head=head, version="v1.2.3")
    second = build_plan(repo, base=base, head=head, version="v1.2.3")

    assert first == second
    assert render_detail_messages(first) == render_detail_messages(second)


def test_every_commit_carries_its_touched_paths_and_deployment_bundles(
    tmp_path: Path,
) -> None:
    """Given commits touching different bundles / When the plan is built /
    Then each commit names its own sha, subject, paths and bundle set."""
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _ = (repo / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: changed deterministic demo skill\n---\n",
        encoding="utf-8",
    )
    _git(repo, "commit", "-am", "스킬 본문을 고친다")
    skill_sha = _git(repo, "rev-parse", "HEAD")
    _ = (repo / "automation" / "pkg" / "watch.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "docs").mkdir()
    _ = (repo / "docs" / "guide.md").write_text("public docs\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "래퍼와 문서를 함께 고친다")
    _git(repo, "commit", "--allow-empty", "-m", "파일을 건드리지 않는 커밋")
    head = _git(repo, "rev-parse", "HEAD")

    plan = build_plan(repo, base=base, head=head, version="v1.2.3")

    assert [(commit.subject, commit.bundles) for commit in plan.commits] == [
        ("스킬 본문을 고친다", ("skill:demo",)),
        ("래퍼와 문서를 함께 고친다", ("home:automation/pkg", "repo")),
        ("파일을 건드리지 않는 커밋", ()),
    ]
    assert plan.commits[0].sha12 == skill_sha[:12]
    assert plan.commits[1].paths == ("automation/pkg/watch.py", "docs/guide.md")


def test_commit_bundles_and_release_surfaces_share_one_path_rule(tmp_path: Path) -> None:
    """Given root and runtime paths / When the plan is built /
    Then the commit bundles and the release surfaces name the same bundles."""
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "automation" / "systemd").mkdir()
    _ = (repo / "automation" / "systemd" / "demo.service").write_text("[Unit]\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "노드 유닛을 추가한다")
    _ = (repo / "automation" / "helper.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "런타임 도우미를 추가한다")
    head = _git(repo, "rev-parse", "HEAD")

    plan = build_plan(repo, base=base, head=head, version="v1.2.3")

    assert [commit.bundles for commit in plan.commits] == [("root",), ("runtime",)]
    surfaces = dict(plan.surface_digests)
    assert "root" in surfaces
    assert "runtime" in surfaces
