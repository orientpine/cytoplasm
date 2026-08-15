"""스킬 마운트 드리프트 판정 — '머지됐지만 배포 안 됨'을 조용하지 않게 만든다.

배경(2026-08-01 실측): 릴리스는 origin/main(79faef4)로 수렴해 있었는데 스킬 마운트 5개가
이틀째 옛 내용이었다. `611595f`가 건드린 6개 중 doctype·mail만 배포되고 나머지는 남은
부분 배포였다. ff-pull 차단은 시끄럽게 실패하지만 이쪽은 아무 신호도 없어 발견이 늦는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.skill_mount_drift import DriftError, inspect_mounts  # noqa: E402
from automation.skill_review import skill_digest  # noqa: E402


def _skill(root: Path, name: str, body: str = "x") -> str:
    """릴리스 트리에 스킬 하나를 만들고 그 digest를 돌려준다."""
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n", encoding="utf-8")
    return skill_digest(directory)


def _mount(live: Path, name: str, digest: str) -> None:
    """`deploy-skill.sh`가 만드는 것과 같은 모양의 심링크를 놓는다."""
    release = live.parent / "releases" / name / digest
    release.mkdir(parents=True, exist_ok=True)
    live.mkdir(parents=True, exist_ok=True)
    (live / name).symlink_to(release)


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "runtime", tmp_path / "skills" / "live"


def test_every_mount_matching_the_release_is_clean(tree: tuple[Path, Path]) -> None:
    # Given: 릴리스의 두 스킬이 그대로 마운트돼 있다
    runtime, live = tree
    for name in ("calendar", "mail"):
        _mount(live, name, _skill(runtime, name))
    # When/Then: 드리프트 없음
    report = inspect_mounts(runtime, live, exempt=frozenset())
    assert report.clean
    assert report.stale == () and report.unmounted == () and report.orphaned == ()


def test_a_mount_whose_content_moved_on_is_reported_stale(tree: tuple[Path, Path]) -> None:
    # Given: calendar 가 예전 내용으로 마운트돼 있고 릴리스는 앞서 나갔다 (실측된 그 상황)
    runtime, live = tree
    fresh = _skill(runtime, "calendar", body="새 내용")
    _mount(live, "calendar", "0" * 64)
    # When
    report = inspect_mounts(runtime, live, exempt=frozenset())
    # Then: 어느 스킬이, 무엇에서 무엇으로 어긋났는지까지 나와야 조치할 수 있다
    assert not report.clean
    assert report.stale == (("calendar", fresh, "0" * 64),)


def test_a_merged_but_never_mounted_skill_is_drift(tree: tuple[Path, Path]) -> None:
    # Given: 릴리스에는 있으나 마운트된 적 없는 스킬 — '머지됐지만 배포 안 됨'의 다른 얼굴
    runtime, live = tree
    _skill(runtime, "brand-new")
    live.mkdir(parents=True)
    # When/Then
    report = inspect_mounts(runtime, live, exempt=frozenset())
    assert not report.clean
    assert report.unmounted == ("brand-new",)


def test_an_exempt_skill_stays_clean_while_unmounted(tree: tuple[Path, Path]) -> None:
    # Given: 영원히 배포하지 않는 데모 스킬. 예외가 없으면 헬스체크가 영구 적색이 된다.
    runtime, live = tree
    _skill(runtime, "hello-autophagy")
    live.mkdir(parents=True)
    # When/Then
    report = inspect_mounts(runtime, live, exempt=frozenset({"hello-autophagy"}))
    assert report.clean
    assert report.unmounted == ()


def test_exemption_never_hides_a_stale_mount(tree: tuple[Path, Path]) -> None:
    # Given: 예외 목록에 있지만 실제로 마운트돼 있고 내용이 어긋난 스킬.
    # 예외는 '배포하지 않음'을 뜻하지 '틀려도 좋음'을 뜻하지 않는다.
    runtime, live = tree
    fresh = _skill(runtime, "hello-autophagy")
    _mount(live, "hello-autophagy", "1" * 64)
    # When/Then
    report = inspect_mounts(runtime, live, exempt=frozenset({"hello-autophagy"}))
    assert not report.clean
    assert report.stale == (("hello-autophagy", fresh, "1" * 64),)


def test_a_mount_with_no_skill_in_the_release_is_orphaned(tree: tuple[Path, Path]) -> None:
    # Given: 릴리스에서 사라진 스킬이 아직 마운트돼 있다
    runtime, live = tree
    (runtime / "skills").mkdir(parents=True)
    _mount(live, "retired", "2" * 64)
    # When/Then
    report = inspect_mounts(runtime, live, exempt=frozenset())
    assert not report.clean
    assert report.orphaned == ("retired",)


def test_a_missing_release_tree_fails_closed(tree: tuple[Path, Path]) -> None:
    # Given: 릴리스 트리가 없다 — '드리프트 없음'으로 읽으면 조용한 거짓 안심이 된다
    runtime, live = tree
    live.mkdir(parents=True)
    # When/Then
    with pytest.raises(DriftError):
        inspect_mounts(runtime, live, exempt=frozenset())


def test_a_missing_live_root_fails_closed(tree: tuple[Path, Path]) -> None:
    # Given: 마운트 루트가 없다 — 마찬가지로 clean 이 아니라 오류다
    runtime, live = tree
    _skill(runtime, "calendar")
    # When/Then
    with pytest.raises(DriftError):
        inspect_mounts(runtime, live, exempt=frozenset())


def test_a_dangling_mount_symlink_is_stale_not_a_crash(tree: tuple[Path, Path]) -> None:
    # Given: 심링크는 있는데 대상이 사라졌다(손상). 부재가 아니라 손상이므로 clean 이 아니다.
    runtime, live = tree
    fresh = _skill(runtime, "calendar")
    _mount(live, "calendar", "3" * 64)
    (live.parent / "releases" / "calendar" / ("3" * 64)).rmdir()
    # When/Then
    report = inspect_mounts(runtime, live, exempt=frozenset())
    assert not report.clean
    assert report.stale == (("calendar", fresh, "3" * 64),)


def test_report_lists_every_drifted_skill_sorted(tree: tuple[Path, Path]) -> None:
    # Given: 부분 배포 — 일부는 맞고 일부는 어긋난 실제 형태
    runtime, live = tree
    good = _skill(runtime, "mail")
    _mount(live, "mail", good)
    for name in ("wiki", "calendar", "todo"):
        _skill(runtime, name)
        _mount(live, name, "4" * 64)
    # When
    report = inspect_mounts(runtime, live, exempt=frozenset())
    # Then: 결정론적 순서여야 증적으로 쓸 수 있다
    assert [entry[0] for entry in report.stale] == ["calendar", "todo", "wiki"]



def test_a_non_symlink_entry_in_the_live_root_is_not_a_skill(tree: tuple[Path, Path]) -> None:
    # Given: 마운트 루트에 스킬이 아닌 디렉터리가 있다.
    # 실측(2026-08-01): 노드의 live 루트에 root 소유 `.hub` 가 있어 첫 실행이
    # 그것을 고아 마운트로 오탐했다. 마운트는 심링크만이다.
    runtime, live = tree
    _mount(live, "mail", _skill(runtime, "mail"))
    (live / ".hub").mkdir()
    # When/Then: 오탐 없이 깨끗해야 한다
    report = inspect_mounts(runtime, live, exempt=frozenset())
    assert report.clean
    assert report.orphaned == ()


def test_a_directory_replacing_a_skill_symlink_still_surfaces(tree: tuple[Path, Path]) -> None:
    # Given: 심링크가 진짜 디렉터리로 바꾸어치기었다(손상).
    # 심링크만 센다고 해서 이 손상이 조용히 통과하면 안 된다.
    runtime, live = tree
    _skill(runtime, "mail")
    live.mkdir(parents=True)
    (live / "mail").mkdir()
    # When/Then: 마운트 집합에서 빠져 미배포로 잡힌다
    report = inspect_mounts(runtime, live, exempt=frozenset())
    assert not report.clean
    assert report.unmounted == ("mail",)