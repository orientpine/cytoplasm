"""노드에서 배포 전량을 관측·판정한다 (RC-3) — 릴리스 트리에 실려 그 자리에서 돈다.

관측과 판정이 **같은 세대의 코드**여야 한다: 워크스테이션 체크아웃이 노드 릴리스보다
앞서거나 뒤처져 있을 수 있으므로, 이 프로브는 `/srv/autophagy-agent-current` 안의
자기 사본으로 실행되어 그 릴리스가 스스로를 판정하게 한다(`deploy_all.sh` 가 ssh 로
부른다). 판정 로직은 `automation.deploy_all`(순수)이, 마운트 대조는 기존
`skill_mount_drift.inspect_mounts` 가, 홈 배포물 목록은 `watcher_manifest` 의 파생
매니페스트가 소유한다 — 여기는 관측 수집과 배선만 남는다(사본 0).

exit: 0 clean · 1 drift · 4 관측 불가(fail-closed — 보지 못한 것은 깨끗한 것이 아니다)
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

if __package__ in (None, ""):  # pragma: no cover - 스크립트로 직접 실행될 때만
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation import deploy_all  # noqa: E402
from automation.skill_mount_drift import DriftError, inspect_mounts  # noqa: E402
from automation.watcher_manifest import CENTRAL_MANIFEST, ManifestError, parse_rows  # noqa: E402

#: 계정·목적지는 sudo 명령에 박히므로 안전하게 인용할 수 없는 값은 관측하지 않는다 —
#: watcher_drift_probe 와 같은 fail-closed(관측 불가 = "?" = 판정 실패).
_SAFE: Final = re.compile(r"^[A-Za-z0-9_./-]+$")

HomeReader = Callable[[str, str], str]


def _read_home(account: str, destination: str) -> str:
    """계정 홈 배포본의 sha256. "" = 없음 · "?" = 읽지 못함(sudo 거부·불통)."""
    if not _SAFE.match(account) or not _SAFE.match(destination):
        return "?"
    proc = subprocess.run(
        (
            "sudo", "-n", "-u", account, "-H", "bash", "-c",
            f'path="$HOME/{destination}"; '
            '[[ -e "$path" ]] || exit 3; '
            'set -o pipefail; sha256sum -- "$path" 2>/dev/null | cut -d" " -f1',
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 3:
        return ""
    if proc.returncode != 0:
        return "?"
    return proc.stdout.strip()


def observations(
    runtime_root: Path, live_root: Path, home_reader: HomeReader
) -> list[str]:
    """관측 줄을 만든다. 읽지 못하면 예외 — 잘린 관측은 판정이 되면 안 된다."""
    lines: list[str] = [f"OBS|release|{runtime_root.resolve().name}"]
    drift = inspect_mounts(runtime_root, live_root)
    lines.append("OBS|mounts|judged")
    for skill, expected, mounted in drift.stale:
        lines.append(f"OBS|mount-stale|{skill}|{expected}|{mounted}")
    for skill in drift.unmounted:
        lines.append(f"OBS|mount-unmounted|{skill}")
    for skill in drift.orphaned:
        lines.append(f"OBS|mount-orphaned|{skill}")
    manifest = runtime_root / CENTRAL_MANIFEST
    for row in parse_rows(manifest.read_text(encoding="utf-8")):
        source_sha = hashlib.sha256((runtime_root / row.source).read_bytes()).hexdigest()
        policy_kind = "optional" if row.policy.startswith("optional") else "required"
        deployed = home_reader(row.account, row.destination)
        lines.append(
            f"OBS|home|{row.account}|{row.destination}|{row.source}"
            f"|{policy_kind}|{source_sha}|{deployed}"
        )
    lines.append("OBS|end")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="배포 전량 관측·판정(읽기 전용)")
    parser.add_argument(
        "--runtime-root", type=Path, default=Path("/srv/autophagy-agent-current")
    )
    parser.add_argument(
        "--live-root", type=Path, default=Path("/srv/autophagy-skills/live")
    )
    parser.add_argument(
        "--format", choices=("report", "actions", "receipt"), default="report"
    )
    args = parser.parse_args(argv)
    try:
        lines = observations(args.runtime_root, args.live_root, _read_home)
        plan = deploy_all.parse_observations(lines)
    except (DriftError, ManifestError, OSError, deploy_all.ObservationError) as error:
        print(f"DEPLOY-ALL-UNVERIFIABLE: {error}", file=sys.stderr)
        return 4
    if args.format == "receipt":
        if not plan.clean:
            print(deploy_all.render_plan(plan), file=sys.stderr)
            return 1
        verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(deploy_all.render_receipt(plan, verified_at=verified_at), end="")
        return 0
    if args.format == "actions":
        actions = deploy_all.render_actions(plan)
        if actions:
            print(actions)
        return 0 if plan.clean else 1
    print(deploy_all.render_plan(plan))
    return 0 if plan.clean else 1


if __name__ == "__main__":  # pragma: no cover - CLI 진입점
    raise SystemExit(main())
