"""봉인된 릴리스가 정말 그 커밋에서 왔는지 확인한다 — 마커의 주장이 아니라 바이트로.

배포 가드(`deploy_provenance.sh`)는 "git 에 없는 코드는 배포하지 않는다"를 워킹트리
blob 대조로 지켜왔다. DG-5 가 런타임을 `.git` 없는 읽기 전용 스냅샷으로 옮기면서 그
대조가 불가능해졌고, 자율 배포 경로(⑦ 워처)는 매 tick `not a git checkout` 으로
막혔다(2026-08-02 실측). 여기서 같은 보장을 릴리스 트리에 대해 다시 세운다.

무엇을 믿지 않는지가 설계의 중심이다:

* `.origin-sha` 는 **믿지 않는다**. 설치기는 호출자가 준 아카이브와 호출자가 준 SHA 를
  그대로 받아 마커에 적으므로, 마커는 "이 커밋에서 왔다"는 검증되지 않은 주장이다.
  마커는 어느 커밋과 대조할지 고르는 데만 쓰고, 그 주장 자체를 트리로 검증한다.
* tracked 목록만 **믿지 않는다**. 커밋에 없는 여분 파일은 목록에 안 잡히면서 배포
  아카이브에는 실려 간다 — 2026-08-01 에 실제로 그렇게 새 파일이 prod 에 닿았다.
  그래서 경로 집합이 정확히 같아야 하고, 유일한 예외는 설치기가 만든 마커 하나다.
* 조상 커밋도 **받지 않는다**. 뒤처진 릴리스는 재조정 타이머가 곧 최신으로 바꾸므로
  미루는 비용은 한 tick 이고, 받아들이는 비용은 소유자가 승인한 것보다 낡은 코드다.

검증 계정은 fetch 자격증명이 없다(최소권한 — agent 는 미러를 읽기만 한다). 그래서
'방금 fetch 한 tip' 대신 ops 재조정 타이머가 2분마다 갱신하는 미러의 `origin/main`
ref 를 기준으로 쓴다. 그 계정에 write 자격증명을 주는 것이 더 큰 후퇴이기 때문이다.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_MARKER_NAME: Final = ".origin-sha"
_OID_LENGTH: Final = 40
_BLOB_MODES: Final = frozenset({"100644", "100755"})
_EXECUTABLE_MODE: Final = "100755"
_WRITE_BITS: Final = 0o222


@dataclass(frozen=True, slots=True)
class Verdict:
    """통과 여부와, 통과하지 못했다면 어느 경로 때문인지."""

    ok: bool
    reason: str


def _blocked(reason: str) -> Verdict:
    return Verdict(False, reason)


def _git(git_root: Path, *args: str) -> str | None:
    """미러는 읽기 전용 객체 저장소로만 쓴다 — 워킹트리는 보지도 실행하지도 않는다.

    ``safe.directory`` 를 그 경로 하나에만 명시하는 이유: 자율 배포 경로의 특권 헬퍼는
    ``env -i … HOME=/root`` 로 파이프라인을 띄운다(약화 변수를 통째로 없애려는 의도된
    선택). 그래서 git 은 실행 계정의 gitconfig 를 잃고 ops 소유인 미러를 dubious
    ownership 으로 거부했다 — 2026-08-03 실측으로 승인된 배포가 매 tick 조용히 멈췄다.
    여기서 하는 일은 객체 읽기뿐이라 워킹트리도 훅도 건드리지 않으므로, 호출자가
    지정한 그 디렉터리 하나만 안전하다고 선언한다."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, paths supplied by the caller
            ("git", "-c", f"safe.directory={git_root}", "-C", str(git_root), *args),
            capture_output=True,
            text=False,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="surrogateescape")


def _blob_oid(path: Path) -> str:
    data = path.read_bytes()
    header = b"blob %d\0" % len(data)
    return hashlib.sha1(header + data).hexdigest()  # noqa: S324 - this IS git's blob oid


def _marker_sha(release: Path) -> str | None:
    marker = release / _MARKER_NAME
    if marker.is_symlink() or not marker.is_file():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if len(value) != _OID_LENGTH or any(char not in "0123456789abcdef" for char in value):
        return None
    return value


def _expected_tree(git_root: Path, sha: str) -> dict[str, tuple[str, str]] | None:
    """커밋이 선언하는 (경로 -> 모드, blob oid) 전체.

    NUL 열거라 한글·공백 경로가 C-escape 로 인용되지 않는다 — 기본 인용을 그대로
    쓰면 이 리포의 한글 문서 45개가 전부 불일치로 잡힌다(2026-08-02 실측).
    oid 를 여기서 함께 받아 파일당 git 호출을 없앱다 — 1500개 서브프로세스는
    2분 주기 워처가 감당할 비용이 아니다.
    """
    listing = _git(git_root, "ls-tree", "-r", "-z", sha)
    if listing is None:
        return None
    entries: dict[str, tuple[str, str]] = {}
    for record in listing.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) != 3 or not path:
            return None
        entries[path] = (fields[0], fields[2])
    return entries


def _actual_paths(release: Path) -> tuple[set[str], str | None]:
    """릴리스가 실제로 담고 있는 것. 일반 파일이 아닌 항목은 즉시 거부 사유가 된다."""
    found: set[str] = set()
    for path in release.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(release).as_posix()
        if path.is_symlink() or not path.is_file():
            return found, relative
        found.add(relative)
    return found, None


def verify_release(release: Path, *, git_root: Path, reference: str = "origin/main") -> Verdict:
    """릴리스 트리 전체가 배포 기준 커밋과 같을 때만 통과한다."""
    if not release.is_dir():
        return _blocked(f"release is not a directory: {release}")
    marker = _marker_sha(release)
    if marker is None:
        return _blocked(f"{_MARKER_NAME} is missing or malformed in {release}")
    if marker != release.name:
        return _blocked(f"{_MARKER_NAME} ({marker}) does not match the release directory name")

    tip = (_git(git_root, "rev-parse", "--verify", "--quiet", reference) or "").strip()
    if not tip:
        return _blocked(f"cannot read {reference} from {git_root}")
    if tip != marker:
        return _blocked(f"release {marker[:12]} is not the {reference} tip {tip[:12]}")
    if _git(git_root, "cat-file", "-e", f"{marker}^{{commit}}") is None:
        return _blocked(f"commit {marker[:12]} is unavailable in {git_root}")

    expected = _expected_tree(git_root, marker)
    if expected is None:
        return _blocked(f"cannot enumerate the tree of {marker[:12]}")
    actual, offender = _actual_paths(release)
    if offender is not None:
        return _blocked(f"release contains a non-regular entry: {offender}")

    extra = sorted(actual - set(expected) - {_MARKER_NAME})
    if extra:
        return _blocked(f"release has files absent from the commit: {', '.join(extra[:5])}")
    missing = sorted(set(expected) - actual)
    if missing:
        return _blocked(f"release is missing committed files: {', '.join(missing[:5])}")

    for relative, (mode, committed_oid) in sorted(expected.items()):
        if mode not in _BLOB_MODES:
            return _blocked(f"unsupported entry mode {mode} for {relative}")
        path = release / relative
        try:
            permissions = path.stat().st_mode
            oid = _blob_oid(path)
        except OSError:
            return _blocked(f"cannot read {relative}")
        if permissions & _WRITE_BITS:
            return _blocked(f"release file is writable: {relative}")
        if bool(permissions & 0o111) != (mode == _EXECUTABLE_MODE):
            return _blocked(f"executable bit disagrees with the commit: {relative}")
        if oid != committed_oid:
            return _blocked(f"release file differs from the commit: {relative}")
    return Verdict(True, f"{len(expected)} file(s) match {reference} at {marker[:12]}")


def main(argv: list[str] | None = None) -> int:
    """배포 가드가 릴리스 모드에서 부르는 진입점 — 통과 0, 차단 1."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--release", required=True, type=Path)
    _ = parser.add_argument("--git-root", required=True, type=Path)
    _ = parser.add_argument("--reference", default="origin/main")
    args = parser.parse_args(argv)
    verdict = verify_release(args.release, git_root=args.git_root, reference=args.reference)
    stream = sys.stdout if verdict.ok else sys.stderr
    print(f"[release-provenance] {'OK' if verdict.ok else 'DEPLOY-BLOCK'}: {verdict.reason}", file=stream)
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
