"""도리 기본 설치의 업데이트 — 이 체크아웃을 서명된 최신 릴리스 태그로 옮기고 도리 설치기를 다시 돈다.

`python -m automation.install.update` 는 리눅스·윈도우 같은 명령이다. 서명 확인은 노드 리컨실러와 같은
`update_trust.resolve_signed_update`(업데이트 신뢰키 서명·주체·태그 경합)와 같은 바닥 규칙(`update_trust_state`,
더 낮은 판·같은 판 다른 커밋 거부)을 쓴다. 노드와 달리 root 가 없는 계정 설치라 신뢰 파일과 바닥은 계정의
상태 폴더(기본 `~/.cytoplasm`)에 둔다.

순서: 신뢰 파일 확인(처음이면 공지 지문과 같은 키만 들인다) → 추적 파일에 고친 것이 없는지 → 원격의 서명 태그
중 가장 높은 판 확인 → 그 커밋으로 detach → 바닥 올림 → 새 트리의 `automation.install.dori --update` 를 새
프로세스로 실행(새 판의 고정 포크로 옮기고 의존성·상시 실행을 맞춘다). 하나라도 실패하면 거기서 멈춘다.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from automation.dori_pin import REPO_ROOT
from automation.install.allowed_signers import (
    GIT_SIGNATURE_NAMESPACE,
    SignerEntry,
    TrustKeyError,
    fingerprint,
    fingerprints_match,
    parse_public_key,
)
from automation.update_trust import UPDATE_TRUST_PRINCIPAL, UpdateTrustError, resolve_signed_update
from automation.update_trust_state import ReleaseFloorError, advance_release_floor, privileged_advance_release_floor

SIGNERS_NAME: Final = "update-allowed-signers"
FLOOR_NAME: Final = "release-floor.json"


class UpdateError(RuntimeError):
    """업데이트를 멈출 이유 — 메시지 첫 낱말이 판정 표식이다."""


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(("git", "-C", str(repo), *args), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        tail = (done.stderr.strip().splitlines() or ["no output"])[-1]
        raise UpdateError(f"CYTO-UPDATE-GIT-FAIL: git {args[0]} ({tail})")
    return done.stdout.strip()


def trust(signers: Path, key: Path | None, expected: str | None) -> str:
    if key is None:
        if not signers.is_file():
            raise UpdateError(
                f"CYTO-UPDATE-NO-TRUST: {signers} 이 없다 — 릴리스의 update-trust.pub 와 README 의 공지 지문을 "
                "--trust-key 와 --expect-fingerprint 로 한 번 준다"
            )
        line = signers.read_text(encoding="utf-8").split()
        return fingerprint(parse_public_key(" ".join(line[2:] if line[1].startswith("namespaces=") else line[1:])))
    if not expected:
        raise UpdateError("CYTO-UPDATE-NO-FINGERPRINT: --trust-key 에는 키와 다른 경로로 받은 --expect-fingerprint 가 꼭 필요하다")
    try:
        public = parse_public_key(key.read_text(encoding="utf-8"))
    except (OSError, TrustKeyError) as error:
        raise UpdateError(f"CYTO-UPDATE-TRUST-KEY-INVALID: {key} ({error})") from error
    actual = fingerprint(public)
    if not fingerprints_match(actual, expected):
        raise UpdateError(f"CYTO-UPDATE-FINGERPRINT-MISMATCH: 받은 키 {actual} != 공지 {expected} — 아무것도 바꾸지 않았다")
    line = SignerEntry(UPDATE_TRUST_PRINCIPAL, public, GIT_SIGNATURE_NAMESPACE).line() + "\n"
    if signers.is_file() and signers.read_text(encoding="utf-8") != line:
        raise UpdateError(
            f"CYTO-UPDATE-TRUST-DIFFERS: {signers} 이 이미 다른 키를 믿는다 — 신뢰키 회전은 그 파일을 확인하고 직접 바꾼다"
        )
    signers.parent.mkdir(parents=True, exist_ok=True)
    _ = signers.write_text(line, encoding="utf-8")
    return actual


def run_dori(dori_args: Sequence[str]) -> int:
    # 새 판의 설치기를 새 프로세스로 돈다: 이 프로세스가 읽어 둔 옛 모듈을 쓰지 않는다.
    env = {**os.environ, "PYTHONUTF8": "1"}
    return subprocess.run((sys.executable, "-m", "automation.install.dori", "--update", *dori_args),
                          cwd=REPO_ROOT, env=env, check=False).returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m automation.install.update",
        description="이 체크아웃을 서명된 최신 릴리스로 옮기고 도리 설치기를 --update 로 다시 돈다",
    )
    _ = parser.add_argument("--state", type=Path, default=Path.home() / ".cytoplasm",
                            help="신뢰 파일·바닥을 두는 계정 폴더(기본 ~/.cytoplasm)")
    _ = parser.add_argument("--trust-key", type=Path, default=None, help="처음 한 번: 릴리스의 update-trust.pub")
    _ = parser.add_argument("--expect-fingerprint", default=None, help="README·릴리스 노트에 공지된 지문")
    _ = parser.add_argument("--dry-run", action="store_true", help="어느 판으로 갈지만 보이고 아무것도 바꾸지 않는다")
    args, dori_args = parser.parse_known_args(argv)
    signers, floor = args.state / SIGNERS_NAME, args.state / FLOOR_NAME
    try:
        if args.dry_run and args.trust_key is not None and not signers.is_file():
            raise UpdateError("CYTO-UPDATE-DRY-RUN-NEEDS-TRUST: 신뢰 파일이 없어 계획 확인도 쓰기 없이 할 수 없다")
        print(f"CYTO-TRUST {trust(signers, args.trust_key, args.expect_fingerprint)} {signers}")
        if _git(REPO_ROOT, "status", "--porcelain", "--untracked-files=no"):
            raise UpdateError(f"CYTO-UPDATE-DIRTY: {REPO_ROOT} 에 고친 추적 파일이 있다 — 커밋하거나 되돌린 뒤 다시")
        try:
            release = resolve_signed_update(REPO_ROOT, signers, floor_path=floor)
            advance_release_floor(floor, release.tag, release.commit_sha)
        except (UpdateTrustError, ReleaseFloorError) as error:
            raise UpdateError(f"CYTO-UPDATE-UNTRUSTED: {error}") from error
        head = _git(REPO_ROOT, "rev-parse", "HEAD")
        print(f"CYTO-UPDATE-TARGET {release.tag} {release.commit_sha} (지금 {head[:12]})")
        if args.dry_run:
            return 0
        if head != release.commit_sha:
            _ = _git(REPO_ROOT, "fetch", "--quiet", "--no-tags", "origin", f"+refs/tags/{release.tag}:refs/tags/{release.tag}")
            _ = _git(REPO_ROOT, "checkout", "--quiet", "--detach", release.commit_sha)
            if (moved := _git(REPO_ROOT, "rev-parse", "HEAD")) != release.commit_sha:
                raise UpdateError(f"CYTO-UPDATE-MISMATCH: want {release.commit_sha} got {moved}")
            print(f"CYTO-UPDATE-MOVED {head[:12]} -> {release.tag} {release.commit_sha[:12]}")
        else:
            print(f"CYTO-UPDATE-CURRENT {release.tag}")
        try:
            privileged_advance_release_floor(floor, release.tag, release.commit_sha)
        except ReleaseFloorError as error:
            raise UpdateError(f"CYTO-UPDATE-FLOOR: {error}") from error
    except UpdateError as error:
        print(error)
        return 2
    code = run_dori(dori_args)
    print(f"CYTO-UPDATED {release.tag} rc={code}" if code == 0 else f"CYTO-UPDATE-INSTALL-FAIL {release.tag} rc={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
