r"""PR 로 들어온 커밋도 서명 태그를 받아야 프로덕션이 전진한다.

리컨실러(`converge_origin_main.sh`)는 인자를 받지 않는 것이 계약이라(MD-1) 설치할 sha 를
서명으로만 정한다 — `origin/main` HEAD 가 annotated 서명 태그의 peel 대상일 때만 수렴한다.
그 태그를 자르는 코드는 `land.sh` 안에만 있었고, 브랜치 작업은 land 가 아니라 PR 머지로
main 에 도달하므로 PR 경로에는 그 단계가 아예 없었다. 2026-08-20 실측: PR 6건이 태그 없이
들어가 리컨실러가 132회 연속 실패했고 프로덕션은 2커밋 뒤에 얼어 있었다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_LIB = _REPO / "automation" / "release_tag_lib.sh"
_COMMAND = _REPO / "automation" / "release-tag.sh"
_LAND = _REPO / "automation" / "land.sh"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(cwd), *args),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    ).stdout.strip()


def _origin_with_commits(tmp_path: Path, count: int = 2) -> tuple[Path, Path]:
    """bare origin + 그것을 clone 한 작업 저장소."""
    origin = tmp_path / "origin.git"
    _ = subprocess.run(("git", "init", "--bare", "-b", "main", str(origin)), check=True,
                       capture_output=True)
    work = tmp_path / "work"
    _ = subprocess.run(("git", "clone", str(origin), str(work)), check=True, capture_output=True)
    # annotated 태그는 tagger 신원을 요구한다. 개발 머신에는 전역 설정이 있지만 CI 에는
    # 없어서, 심어두지 않으면 여기서만 `signing tag failed` 로 갈린다.
    _git(work, "config", "user.name", "release-tag-test")
    _git(work, "config", "user.email", "release-tag-test@example.invalid")
    for index in range(count):
        _ = (work / f"f{index}").write_text(f"{index}\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", f"c{index}")
    _git(work, "push", "-u", "origin", "main")
    return origin, work


def _run_lib(
    work: Path,
    sha: str,
    *,
    key: Path | None,
    version: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    env["UPDATE_TRUST_SIGNING_KEY"] = str(key) if key else str(work / "absent.pub")
    requested = f' "{version}"' if version is not None else ""
    return subprocess.run(
        (
            "bash",
            "-c",
            f'source "{_LIB}"; ensure_signed_tag "{work}" "{sha}"{requested}',
        ),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _next_tag(work: Path, bump: str | None = None) -> subprocess.CompletedProcess[str]:
    argument = f' "{bump}"' if bump is not None else ""
    return subprocess.run(
        ("bash", "-c", f'source "{_LIB}"; next_release_tag "{work}"{argument}'),
        capture_output=True,
        text=True,
        check=False,
    )


def _signing_key(tmp_path: Path) -> Path:
    key = tmp_path / "signer"
    _ = subprocess.run(("ssh-keygen", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)),
                       check=True, capture_output=True)
    return key


# --- ensure_signed_tag -----------------------------------------------------------


def test_a_commit_without_a_tag_gets_one(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")
    key = _signing_key(tmp_path)

    result = _run_lib(work, head, key=key)

    assert result.returncode == 0, result.stderr
    assert "signed release tag v1.0.0" in result.stderr
    # 원격에서 peel 이 그 커밋을 가리켜야 리컨실러가 본다.
    peeled = _git(work, "ls-remote", "--tags", "origin")
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in peeled


def test_a_commit_already_tagged_is_left_alone(tmp_path: Path) -> None:
    """멱등 — PR 머지 루틴이 매번 불러도 태그가 늘지 않아야 한다."""
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")
    key = _signing_key(tmp_path)
    _ = _run_lib(work, head, key=key)

    result = _run_lib(work, head, key=key)

    assert result.returncode == 0
    assert "already released as v1.0.0" in result.stderr
    assert _git(work, "ls-remote", "--tags", "origin").count("refs/tags/v1.0.0\t") <= 1


def test_a_missing_signing_key_fails_loudly(tmp_path: Path) -> None:
    """키 없이 조용히 넘어가면 노드는 이유 없이 계속 선다."""
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    result = _run_lib(work, head, key=None)

    assert result.returncode != 0
    assert "no update-trust signing key" in result.stderr


def test_the_version_advances_from_the_latest_tag(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    key = _signing_key(tmp_path)
    _ = _run_lib(work, _git(work, "rev-parse", "HEAD"), key=key)
    _ = (work / "next").write_text("x\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "next")
    _git(work, "push", "origin", "main")

    result = _run_lib(work, _git(work, "rev-parse", "HEAD"), key=key)

    assert result.returncode == 0
    assert "v1.0.1" in result.stderr


def test_next_version_defaults_to_patch_and_selects_minor_or_major(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    _git(work, "tag", "v2.3.4")
    _git(work, "push", "origin", "v2.3.4")

    assert _next_tag(work).stdout.strip() == "v2.3.5"
    assert _next_tag(work, "patch").stdout.strip() == "v2.3.5"
    assert _next_tag(work, "minor").stdout.strip() == "v2.4.0"
    assert _next_tag(work, "major").stdout.strip() == "v3.0.0"


def test_prerelease_suffix_does_not_stall_the_release_series(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    _git(work, "tag", "v2.3.4")
    _git(work, "tag", "v9.0.0-rc1")
    _git(work, "push", "origin", "--tags")

    result = _next_tag(work)

    assert result.returncode == 0
    assert result.stdout.strip() == "v2.3.5"


def test_requested_version_must_match_existing_tag_at_head(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")
    key = _signing_key(tmp_path)
    first = _run_lib(work, head, key=key, version="v1.0.0")
    assert first.returncode == 0, first.stderr

    result = _run_lib(work, head, key=key, version="v1.0.1")

    assert result.returncode != 0
    assert "v1.0.0" in result.stderr
    assert "v1.0.1" in result.stderr


# --- 배선 -------------------------------------------------------------------------


def test_land_and_the_command_share_one_implementation() -> None:
    """사본이 갈라지면 PR 경로와 land 경로가 서로 다른 태그를 자르게 된다."""
    land = _LAND.read_text(encoding="utf-8")
    command = _COMMAND.read_text(encoding="utf-8")

    assert "release_tag_lib.sh" in land
    assert "release_tag_lib.sh" in command
    assert "\nensure_signed_tag() {" not in land, "land.sh 가 자체 사본을 다시 들고 있다"
    assert "\nensure_signed_tag() {" not in command


def test_the_command_verifies_head_did_not_move() -> None:
    """태그가 HEAD 를 빗나가면 노드는 그대로 선다 — 성공으로 보고하면 안 된다."""
    text = _COMMAND.read_text(encoding="utf-8")

    assert "moved to" in text
    assert "re-run this command" in text


def test_the_command_is_executable() -> None:
    assert os.access(_COMMAND, os.X_OK), "PR 머지 루틴이 직접 실행한다"
