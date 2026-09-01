"""계정 홈 배포물 선언의 단일 정의 — 선언은 배포기 옆에, 중앙 표는 파생물로 (RC-1).

`configs/watcher-deploy-manifest.txt` 는 계정 홈 표면(no-agent cron 래퍼·게이트웨이
플러그인)의 유일한 탐지 원천이었지만 **사람이 손으로 유지하는 표**였다 — 새 배포물을
만들고 등록을 잊으면 배포되지 않아도 탐지되지 않았고(2026-08-28 meeting 플러그인 5일
침묵 실측), 그 누락을 말해 주는 것이 아무것도 없었다. 등록을 잊을 수 없게 만드는 길은
표를 더 잘 관리하는 것이 아니라 표를 **파생물로 강등**하는 것이다: 선언을 각 배포기
(`deploy.sh`) 옆 `deploy-manifest.txt` 로 옮기고, 중앙 표는 선언들의 결정적 유도 결과로만
존재한다. 배포기를 만들면서 선언을 빠뜨리면 conformance
(`tests/unit/test_watcher_manifest_declarations.py`)가 빌드를 깨뜨린다.

행 형식·의미는 기존 그대로다(`<account>|<source>|<destination>|<policy>`) — 소비자
(`watcher_drift_probe.sh` · `healthcheck_probe_wrapper.sh`)는 계속 중앙 파일 하나만
읽으므로 이 강등은 소비자에게 보이지 않는다.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: 계정 홈에 착지하는 배포물의 모양 — `~/.hermes/scripts/<file>` 와
#: `~/.hermes/plugins/<dir>/<file>`. `scripts/` 만 보던 옛 정규식이 플러그인을 처음부터
#: 놓쳤으므로(2026-08-28), 이 패턴의 사본을 만들지 말고 여기서 import 한다.
HOME_DEPLOYED_PATTERN: Final = re.compile(
    r"\.hermes/(?:scripts/[A-Za-z0-9_.-]+|plugins/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
)

DECLARATION_NAME: Final = "deploy-manifest.txt"
CENTRAL_MANIFEST: Final = "configs/watcher-deploy-manifest.txt"

_HEADER: Final = """\
# GENERATED — 이 파일은 손으로 편집하지 않는다.
#
# 원천은 각 배포기 옆의 선언 파일(<package>/deploy-manifest.txt)이다. 계정 홈 배포물을
# 늘리거나 바꾸려면 그 패키지의 선언을 고친 뒤 다음으로 재생성한다:
#   python3 -m automation.watcher_manifest emit
# tests/unit/test_watcher_manifest_declarations.py 가 선언과 이 파일의 파생 일치를
# byte 단위로 강제한다 — 여기만 고쳐도 RED, 선언만 고치고 emit 을 잊어도 RED.
#
# 형식: <account>|<릴리스 소스 경로>|<홈 기준 목적지>|<policy>
#   policy=required        배포돼 있어야 한다. 없거나 다르면 FAIL.
#   policy=optional:<사유>  없을 수 있다(그 사유가 설계다). 배포돼 있으면 드리프트는 여전히 FAIL.
#
# 이 파일의 바이트는 healthcheck 강제명령 래퍼의 inputs digest 에 들어간다 — 행이 바뀌면
# 노드에서 `automation/healthcheck_probe_wrapper.sh --install` 을 다시 돌려야 하고, 그
# 필요는 healthcheck_wrapper_current 프로브가 지문 대조로 알려준다(조용히 낡지 않는다)."""


class ManifestError(RuntimeError):
    """선언이 형식을 어겼다 — 파싱할 수 없는 선언은 탐지할 수 없는 배포물이다."""


@dataclass(frozen=True, slots=True)
class Row:
    account: str
    source: str
    destination: str
    policy: str

    @property
    def owning_package(self) -> str:
        """소스 경로 앞 두 조각 — 재배포 명령 유도와 같은 규칙(`skills/mail/… → skills/mail`)."""
        return "/".join(self.source.split("/")[:2])

    def line(self) -> str:
        return "|".join((self.account, self.source, self.destination, self.policy))


def parse_rows(text: str) -> tuple[Row, ...]:
    rows: list[Row] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("|", 3)
        if len(parts) != 4 or not all(part.strip() for part in parts):
            raise ManifestError(f"malformed manifest row: {stripped[:80]}")
        rows.append(Row(*(part.strip() for part in parts)))
    return tuple(rows)


def declaration_files(repo: Path) -> tuple[Path, ...]:
    """결정적 순서의 선언 파일 목록 — automation/* 다음 skills/* (경로 정렬)."""
    return tuple(
        sorted(repo.glob(f"automation/*/{DECLARATION_NAME}"))
        + sorted(repo.glob(f"skills/*/{DECLARATION_NAME}"))
    )


def derive_manifest(repo: Path) -> str:
    """선언 파일들의 유일한 유도 결과. 같은 입력이면 byte 까지 같은 출력이다."""
    sections: list[str] = [_HEADER]
    for declaration in declaration_files(repo):
        relative = declaration.relative_to(repo).as_posix()
        rows = parse_rows(declaration.read_text(encoding="utf-8"))
        if not rows:
            # 빈 선언은 "선언 없음"과 구분되지 않는다 — 배포물이 없어졌으면 파일을 지운다.
            raise ManifestError(f"declaration has no rows: {relative}")
        sections.append("\n".join([f"# --- {relative} ---", *(row.line() for row in rows)]))
    return "\n\n".join(sections) + "\n"


def all_rows(repo: Path) -> tuple[Row, ...]:
    return parse_rows(derive_manifest(repo))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    repo = _repo_root()
    central = repo / CENTRAL_MANIFEST
    command = argv[0] if argv else ""
    if command == "emit":
        derived = derive_manifest(repo)
        _ = central.write_text(derived, encoding="utf-8")
        print(f"[watcher-manifest] wrote {CENTRAL_MANIFEST} ({len(parse_rows(derived))} rows)")
        return 0
    if command == "check":
        derived = derive_manifest(repo)
        current = central.read_text(encoding="utf-8") if central.is_file() else ""
        if current == derived:
            print(f"[watcher-manifest] OK: {CENTRAL_MANIFEST} matches its declarations")
            return 0
        print(
            f"[watcher-manifest] DRIFT: {CENTRAL_MANIFEST} does not match the declarations —"
            " run `python3 -m automation.watcher_manifest emit`",
            file=sys.stderr,
        )
        return 1
    print("usage: python3 -m automation.watcher_manifest check|emit", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
