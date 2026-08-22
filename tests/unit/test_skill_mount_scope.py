r"""마운트되는 것과 승인 해시가 덮는 것은 정확히 같은 집합이어야 한다.

`deploy.sh` 는 배포 **도구**다 — 배포는 개발 체크아웃이나 릴리스에서 돌고, 마운트본의
그 파일은 한 번도 실행되지 않는다. 그런데도 digest 에 들어 있었고, 그래서 2026-08-20 에
배포 도구를 고친 변경 하나(PR #191, `skills/*/deploy.sh` 6개 × 8줄)가 **무관한 스킬 6개의**
마운트를 무효화해 소유자 ✅ 를 6번 요구했다.

제외 자체보다 **두 집합이 갈라지지 않는 것**이 중요하다: digest 에서만 빼고 계속 실어
보내면 승인 해시가 덮지 않는 파일이 노드에 올라간다. 그래서 여기서 아카이브 멤버와
digest 대상 파일을 직접 대조한다.
"""

from __future__ import annotations

import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

from automation.skill_review import MOUNT_EXCLUDED_BASENAMES, _skill_files, skill_digest

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY_SKILL = _REPO / "automation" / "deploy-skill.sh"
_PROVENANCE = _REPO / "automation" / "deploy_provenance.sh"


def _archive_members(skill: str) -> set[str]:
    """`deploy-skill.sh` 가 실제로 보내는 스트림의 파일 목록."""
    excluded = ",".join(sorted(MOUNT_EXCLUDED_BASENAMES))
    stream = subprocess.run(
        (
            "bash",
            "-c",
            f'source "{_PROVENANCE}"; '
            f'DEPLOY_ARCHIVE_EXCLUDE_BASENAMES="{excluded}" '
            f'deploy_archive_stream "{_REPO}" "{_REPO}/skills" "{skill}"',
        ),
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=BytesIO(stream), mode="r:gz") as archive:
        return {
            member.name.removeprefix(f"{skill}/")
            for member in archive.getmembers()
            if member.isfile()
        }


def _digest_members(skill: str) -> set[str]:
    skill_dir = _REPO / "skills" / skill
    return {
        path.relative_to(skill_dir).as_posix() for path in _skill_files(skill_dir)
    }


def test_the_archive_and_the_digest_cover_the_same_files() -> None:
    """이것이 깨지면 승인 해시가 덮지 않는 파일이 노드에 올라간다."""
    members = _archive_members("wiki")
    covered = _digest_members("wiki")

    assert members, "아카이브가 비었다 — 대조가 공허해진다"
    assert members == covered, (
        "마운트되는 파일과 승인 해시가 덮는 파일이 다르다. "
        f"아카이브에만: {sorted(members - covered)} / digest 에만: {sorted(covered - members)}"
    )


def test_deploy_sh_is_in_neither() -> None:
    assert "deploy.sh" in MOUNT_EXCLUDED_BASENAMES
    assert "deploy.sh" not in _archive_members("wiki")
    assert "deploy.sh" not in _digest_members("wiki")


def test_touching_the_deploy_tool_no_longer_invalidates_the_mount(tmp_path: Path) -> None:
    """실측된 그 상황 — 배포 도구만 고쳤는데 스킬 6개가 재승인 대상이 됐다."""
    skill = tmp_path / "demo"
    (skill / "scripts").mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
    _ = (skill / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    _ = (skill / "deploy.sh").write_text("echo v1\n", encoding="utf-8")
    before = skill_digest(skill)

    _ = (skill / "deploy.sh").write_text("echo v2 — tooling changed\n", encoding="utf-8")

    assert skill_digest(skill) == before, "배포 도구 변경이 여전히 마운트를 무효화한다"


def test_changing_what_actually_runs_still_invalidates_the_mount(tmp_path: Path) -> None:
    """반대 방향 — 승인 범위를 좁힌 것이지 없앤 것이 아니다."""
    skill = tmp_path / "demo"
    (skill / "scripts").mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
    _ = (skill / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    before = skill_digest(skill)

    _ = (skill / "scripts" / "run.py").write_text("print(2)\n", encoding="utf-8")

    assert skill_digest(skill) != before, "실행되는 코드 변경은 반드시 재승인 대상이어야 한다"


def test_the_shell_reads_the_exclusion_from_python() -> None:
    """이름을 셸에 베껴 적으면 두 집합이 조용히 갈라진다."""
    text = _DEPLOY_SKILL.read_text(encoding="utf-8")

    assert "MOUNT_EXCLUDED_BASENAMES" in text, "셸이 단일 진실을 읽어야 한다"
    assert "DEPLOY_ARCHIVE_EXCLUDE_BASENAMES" in text


def test_the_generic_archive_helper_excludes_nothing_by_default() -> None:
    """이 헬퍼는 스킬 전용이 아니다 — 지정하지 않은 배포 경로는 그대로여야 한다."""
    members = subprocess.run(
        (
            "bash",
            "-c",
            f'source "{_PROVENANCE}"; deploy_archive_stream "{_REPO}" "{_REPO}/skills" wiki',
        ),
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=BytesIO(members), mode="r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}

    assert "wiki/deploy.sh" in names
