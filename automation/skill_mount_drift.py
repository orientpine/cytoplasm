"""마운트된 스킬이 릴리스와 같은 내용인지 판정한다 — "커밋됨 ≠ 배포됨"의 탐지기.

릴리스 수렴(git sha)과 스킬 마운트(내용 해시)는 서로 독립이다. 그래서 릴리스가
`origin/main`에 도달해 있어도 스킬은 옛 내용으로 남을 수 있고, 그 상태는 **아무 신호도
내지 않는다** — ff-pull 차단은 시끄럽게 실패하지만 이쪽은 조용해서, 에이전트는 새 코드가
돈다고 여기지만 실제로는 구버전이 돈다.

실측(2026-08-01): 릴리스는 79faef4(=origin/main)였는데 마운트 5개가 이틀째 옛 내용이었다.
`611595f`가 건드린 6개 중 doctype·mail만 배포되고 calendar·prompt·todo·wiki가 남은
부분 배포였다. 부분 배포는 특히 조용하다 — 배포가 "있었기" 때문에 아무도 다시 보지 않는다.

판정은 `skill_digest`(내용 해시) 대 `readlink live/<skill>`의 해시다. git sha를 쓰지 않는
이유는 셋이다: sha는 리포 전체에 하나뿐이라 **어느 스킬이** 바뀌었는지 말해주지 못하고,
스킬은 sha가 없는 출처(샌드박스·managed 활성화)에서도 배포되며, 마운트는 커밋이 아니라
아티팩트라 물어야 할 질문이 "지금 걸려 있는 내용이 릴리스와 같은가"이기 때문이다.

읽기 전용이다 — 어떤 경로도 쓰지 않고, 배포를 시도하지도 않는다.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in (None, ""):  # pragma: no cover - 스크립트로 직접 실행될 때만
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.skill_review import skill_digest  # noqa: E402

#: 마운트되지 않는 것이 정상인 스킬. 비워두면 헬스체크가 영구 적색이 되고, 영구 적색인
#: 게이트는 아무도 보지 않게 된다. 사유 없이 추가하지 말 것 — 예외는 "배포하지 않음"을
#: 뜻할 뿐이며, 마운트돼 있는데 내용이 어긋난 경우는 예외와 무관하게 STALE로 잡힌다.
DEFAULT_EXEMPT: Final = frozenset({
    "hello-autophagy",   # 온보딩 데모 — 프로덕션에 마운트한 적이 없고 할 계획도 없다
})

#: 드리프트 있음. 4는 판정 불가(fail-closed)로 두어 "깨끗함"과 절대 섞이지 않게 한다.
DRIFT_EXIT: Final = 1
UNVERIFIABLE_EXIT: Final = 4


class DriftError(Exception):
    """판정에 필요한 것을 읽지 못했다 — 조용한 PASS 대신 시끄러운 실패."""


@dataclass(frozen=True, slots=True)
class MountDrift:
    """한 노드의 마운트 상태. 셋 다 비어야 깨끗하다."""

    stale: tuple[tuple[str, str, str], ...]      # (스킬, 릴리스 digest, 마운트 digest)
    unmounted: tuple[str, ...]                   # 릴리스에 있으나 마운트된 적 없음
    orphaned: tuple[str, ...]                    # 마운트돼 있으나 릴리스에 없음

    @property
    def clean(self) -> bool:
        return not (self.stale or self.unmounted or self.orphaned)

    def render(self) -> str:
        """운영자가 그대로 읽고 조치할 수 있는 형태. 경로·본문은 싣지 않는다."""
        if self.clean:
            return "SKILL-MOUNTS-CURRENT: 모든 마운트가 릴리스와 일치"
        lines: list[str] = []
        for skill, expected, mounted in self.stale:
            lines.append(
                f"SKILL-STALE: {skill} — 릴리스 {expected[:16]}… 이지만 마운트는 {mounted[:16]}…"
            )
        for skill in self.unmounted:
            lines.append(f"SKILL-UNMOUNTED: {skill} — 릴리스에 있으나 마운트된 적 없음")
        for skill in self.orphaned:
            lines.append(f"SKILL-ORPHANED: {skill} — 마운트돼 있으나 릴리스에 없음")
        return "\n".join(lines)


def _release_skills(runtime_root: Path) -> dict[str, str]:
    directory = runtime_root / "skills"
    if not directory.is_dir():
        raise DriftError(f"릴리스 스킬 트리를 읽을 수 없음: {directory}")
    found: dict[str, str] = {}
    for path in sorted(directory.iterdir()):
        if path.is_dir() and (path / "SKILL.md").is_file():
            found[path.name] = skill_digest(path)
    return found


def _mounted_skills(live_root: Path) -> dict[str, str]:
    """심링크만 마운트다 — `deploy-skill.sh`가 만드는 것이 심링크이기 때문이다.

    마운트 루트에는 스킬이 아닌 것도 산다(실측: root 소유 `.hub` 디렉터리, ops는
    읽지도 못한다). 그것까지 스킬로 세면 매 회 고아 마운트로 오탐이 난다. 반대로
    심링크가 진짜 디렉터리로 바꾸어치기는 손상은 조용히 사라지지 않는다 — 그 스킬은
    마운트 집합에서 빠져 `unmounted`로 잡힌다.
    """
    if not live_root.is_dir():
        raise DriftError(f"마운트 루트를 읽을 수 없음: {live_root}")
    return {
        path.name: path.readlink().name
        for path in sorted(live_root.iterdir())
        if path.is_symlink()
    }


def inspect_mounts(
    runtime_root: Path, live_root: Path, *, exempt: frozenset[str] = DEFAULT_EXEMPT
) -> MountDrift:
    """릴리스와 마운트를 대조한다. 읽기 전용이며, 읽지 못하면 예외를 던진다."""
    release, mounted = _release_skills(runtime_root), _mounted_skills(live_root)
    return MountDrift(
        stale=tuple(
            (name, release[name], mounted[name])
            for name in sorted(release.keys() & mounted.keys())
            if release[name] != mounted[name]
        ),
        unmounted=tuple(sorted(release.keys() - mounted.keys() - exempt)),
        orphaned=tuple(sorted(mounted.keys() - release.keys())),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="마운트된 스킬이 릴리스와 같은지 확인(읽기 전용)")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--live-root", type=Path, default=Path("/srv/autophagy-skills/live"))
    args = parser.parse_args(argv)
    try:
        report = inspect_mounts(args.runtime_root, args.live_root)
    except DriftError as error:
        print(f"SKILL-MOUNT-UNVERIFIABLE: {error}", file=sys.stderr)
        return UNVERIFIABLE_EXIT
    print(report.render())
    return 0 if report.clean else DRIFT_EXIT


if __name__ == "__main__":  # pragma: no cover - CLI 진입점
    raise SystemExit(main())
