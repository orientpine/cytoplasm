"""Deterministic release surfaces and bounded owner-facing patch notes (VA-1).

The release command runs only for a clean checkout whose ``HEAD`` equals ``origin/main``.
That makes the checkout an exact materialisation of the commit being approved, so skill
digests can reuse :func:`skill_review.skill_digest` byte-for-byte.  Home-artifact ownership
reuses :class:`watcher_manifest.Row`; no second deployment registry exists here.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from automation.skill_review import skill_digest
from automation.watcher_manifest import CENTRAL_MANIFEST, Row, parse_rows


class ReleasePlanError(RuntimeError):
    """The requested git range cannot produce a trustworthy release plan."""


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    version: str
    base: str
    head: str
    tree_digest: str
    changed_paths: tuple[str, ...]
    surface_digests: tuple[tuple[str, str], ...]
    commit_titles: tuple[str, ...]


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *arguments),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleasePlanError(
            f"git {' '.join(arguments[:2])} failed with exit {result.returncode}"
        )
    return result.stdout


def _changed_paths(repo: Path, base: str, head: str) -> tuple[str, ...]:
    raw = subprocess.run(
        ("git", "-C", str(repo), "diff", "--name-only", "-z", f"{base}..{head}"),
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0:
        raise ReleasePlanError("could not enumerate the release diff")
    return tuple(sorted(part.decode("utf-8") for part in raw.stdout.split(b"\0") if part))


def _sha256_lines(rows: tuple[str, ...]) -> str:
    return hashlib.sha256("".join(f"{row}\n" for row in rows).encode("utf-8")).hexdigest()


def _home_package_digest(repo: Path, rows: tuple[Row, ...]) -> str:
    payload: list[str] = []
    for row in sorted(rows, key=lambda item: (item.destination, item.source)):
        try:
            source_digest = hashlib.sha256((repo / row.source).read_bytes()).hexdigest()
        except OSError as error:
            raise ReleasePlanError(f"declared release source is unreadable: {row.source}") from error
        payload.append(f"{source_digest}  {row.destination}")
    return _sha256_lines(tuple(payload))


def _git_paths_digest(repo: Path, head: str, paths: tuple[str, ...]) -> str:
    listing = _git(repo, "ls-tree", "-r", head, "--", *paths)
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def _tree_digest(repo: Path, head: str) -> str:
    tree = _git(repo, "rev-parse", f"{head}^{{tree}}").strip()
    if not tree:
        raise ReleasePlanError("release tree is empty")
    return hashlib.sha256(tree.encode("ascii")).hexdigest()


def build_plan(repo: Path, *, base: str, head: str, version: str) -> ReleasePlan:
    """Build one immutable plan from an exact clean checkout of ``head``."""
    actual = _git(repo, "rev-parse", "HEAD").strip()
    if actual != head:
        raise ReleasePlanError(f"checkout HEAD {actual} differs from release HEAD {head}")
    changed = _changed_paths(repo, base, head)
    manifest_path = repo / CENTRAL_MANIFEST
    try:
        rows = parse_rows(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReleasePlanError(f"deployment manifest is unreadable: {manifest_path}") from error

    surfaces: dict[str, str] = {}
    skills_root = repo / "skills"
    skills = sorted(
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )
    for skill in skills:
        surfaces[f"skill:{skill}"] = skill_digest(repo / "skills" / skill)

    packages = sorted({row.owning_package for row in rows})
    for package in packages:
        package_rows = tuple(row for row in rows if row.owning_package == package)
        surfaces[f"home:{package}"] = _home_package_digest(repo, package_rows)

    covered = {
        path
        for path in changed
        if (
            path.startswith(tuple(f"skills/{skill}/" for skill in skills))
            or any(path == row.source and row.owning_package in packages for row in rows)
        )
    }
    remaining = tuple(path for path in changed if path not in covered)
    rag = tuple(
        path
        for path in remaining
        if path.startswith(("configs/rag/", "automation/rag_ingest/", "automation/rag_stack/"))
    )
    root = tuple(
        path
        for path in remaining
        if path.startswith(("automation/systemd/", "automation/sudoers.d/", "automation/libexec/"))
    )
    runtime = tuple(
        path
        for path in remaining
        if path not in rag and path not in root and path.startswith(("automation/", "configs/"))
    )
    repo_only = tuple(
        path for path in remaining if path not in rag and path not in root and path not in runtime
    )
    tree_digest = _tree_digest(repo, head)
    for name, paths in (
        ("rag", rag),
        ("root", root),
        ("runtime", runtime),
    ):
        if paths:
            surfaces[name] = _git_paths_digest(repo, head, paths)
    if repo_only:
        surfaces["repo"] = tree_digest

    titles = tuple(
        title.strip()
        for title in _git(repo, "log", "--reverse", "--format=%s", f"{base}..{head}").splitlines()
        if title.strip()
    )
    return ReleasePlan(
        version=version,
        base=base,
        head=head,
        tree_digest=tree_digest,
        changed_paths=changed,
        surface_digests=tuple(sorted(surfaces.items())),
        commit_titles=titles,
    )


def render_patch_notes(plan: ReleasePlan, *, commit_limit: int = 20) -> str:
    """Render the bounded Discord copy; the complete plan remains machine-readable."""
    shown = plan.commit_titles[:commit_limit]
    commit_lines = tuple(f"  - {title}" for title in shown) or ("  - 커밋 없음",)
    hidden = len(plan.commit_titles) - len(shown)
    if hidden:
        commit_lines = (*commit_lines, f"  - +{hidden}건")
    return "\n".join(
        (
            f"- 릴리스 범위: `{plan.base[:12]}..{plan.head[:12]}`",
            "- 사용자·운영 변경:",
            *commit_lines,
        )
    )
