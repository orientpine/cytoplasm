"""게이트웨이가 지금 도는 코드와 디스크의 벤더 소스가 어긋났는지 판정한다.

2026-08-18 실측: 벤더 자체 업데이트가 소스를 갈아끼우고도 게이트웨이를 재시동하지 않아
프로세스가 **옛 코드 + 새 파일** 상태로 20시간 돌았고, 첫 도구 호출에서 도구 계층 전체가
멈춘 뒤에야 드러났다. 유닛은 내내 active/running 이었고 Discord 연결도 살아 있었으므로,
liveness 나 연결성에 기대는 프로브는 이 장애를 **원리적으로** 통과시킨다. 같은 날 agent 와
peer 의 벤더 버전이 갈라져 있었는데, 각자 자기 코드끼리는 일관돼 갈라짐 자체도 아무 신호를
내지 않았다 — 「게이트웨이 재시동 규칙」이 두 계정을 쌍으로 다루는 전제가 이미 깨져 있었다.

그래서 두 가지만 본다: (1) 기동 시각보다 새로운 소스가 있는가, (2) 계정들이 같은 소스를
싣고 있는가. 판정 근거는 파일 내용과 mtime 뿐이며, 기동 시각은 호출자가 넘긴다.

이 모듈은 **탐지만** 한다. 재적용과 게이트웨이 쌍 재시동은 외부효과라 소유자 원장 소관이고,
여기서는 벤더 CLI·원격 셸·유닛 관리자를 부르지 않는다(patch_state.py 와 같은 계약).

종료 코드: 0 어긋남 없음 · 1 어긋남 확인 · 2 판정 불가. 판정 불가가 어긋남보다 높다 —
"읽을 수 없었다"를 "괜찮다"로 보고하면 사고가 다음 요청까지 잠복한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

DEFAULT_INSTALL_ROOT: Final = Path.home() / ".hermes" / "hermes-agent"

FRESH: Final = "FRESH"
STALE: Final = "STALE"
CONVERGED: Final = "CONVERGED"
DIVERGED: Final = "DIVERGED"
UNKNOWN: Final = "UNKNOWN"

_IGNORED_DIRECTORIES: Final = frozenset({"__pycache__", ".git", ".venv"})


@dataclass(frozen=True, slots=True)
class Reading:
    check: str
    state: str
    detail: str


def _source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if _IGNORED_DIRECTORIES.isdisjoint(path.parts):
            yield path


def newest_source_mtime(root: Path) -> float | None:
    """None means the tree could not be measured, which is never a pass."""
    if not root.is_dir():
        return None
    newest: float | None = None
    try:
        for path in _source_files(root):
            mtime = path.stat().st_mtime
            newest = mtime if newest is None else max(newest, mtime)
    except OSError:
        return None
    return newest


def source_fingerprint(root: Path) -> str | None:
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    try:
        for path in _source_files(root):
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
    except OSError:
        return None
    return digest.hexdigest()


def probe_staleness(root: Path, started_at: float | None) -> Reading:
    if started_at is None:
        return Reading("staleness", UNKNOWN, f"gateway start time was not supplied for {root}")
    newest = newest_source_mtime(root)
    if newest is None:
        return Reading("staleness", UNKNOWN, f"vendored source is unreadable: {root}")
    if newest > started_at:
        return Reading(
            "staleness",
            STALE,
            f"source mtime {newest:.0f} is newer than gateway start {started_at:.0f}: {root}",
        )
    return Reading("staleness", FRESH, f"gateway start {started_at:.0f} covers source {newest:.0f}")


def probe_divergence(installs: Mapping[str, Path]) -> Reading:
    if len(installs) < 2:
        return Reading("divergence", UNKNOWN, "fewer than two installs were supplied")
    fingerprints = {account: source_fingerprint(root) for account, root in installs.items()}
    unreadable = sorted(account for account, value in fingerprints.items() if value is None)
    if unreadable:
        return Reading("divergence", UNKNOWN, f"unreadable install(s): {', '.join(unreadable)}")
    distinct = set(fingerprints.values())
    if len(distinct) > 1:
        rendered = ", ".join(
            f"{account}={str(value)[:12]}" for account, value in sorted(fingerprints.items())
        )
        return Reading("divergence", DIVERGED, f"accounts carry different source: {rendered}")
    return Reading("divergence", CONVERGED, f"{len(installs)} account(s) carry identical source")


def verdict(readings: Sequence[Reading]) -> int:
    if not readings:
        return 2
    if any(reading.state == UNKNOWN for reading in readings):
        return 2
    if any(reading.state in {STALE, DIVERGED} for reading in readings):
        return 1
    return 0


def parse_start_time(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"unparsable gateway start time: {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def render(readings: Sequence[Reading]) -> str:
    return "\n".join(f"  {reading.state:9s} {reading.check} — {reading.detail}" for reading in readings)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    parser.add_argument("--peer-install-root", type=Path, default=None)
    parser.add_argument("--started-at", default=None)
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)

    started_at: float | None = None
    if arguments.started_at is not None:
        try:
            started_at = parse_start_time(arguments.started_at)
        except ValueError as error:
            print(f"{UNKNOWN}  {error}")
            return 2

    readings = [probe_staleness(arguments.install_root, started_at)]
    if arguments.peer_install_root is not None:
        readings.append(
            probe_divergence(
                {"agent": arguments.install_root, "peer": arguments.peer_install_root}
            )
        )
    outcome = verdict(readings)
    if arguments.json:
        print(
            json.dumps(
                {
                    "verdict": outcome,
                    "readings": [
                        {"check": r.check, "state": r.state, "detail": r.detail} for r in readings
                    ],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(f"hermes gateway runtime @ {arguments.install_root}")
        print(render(readings))
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
