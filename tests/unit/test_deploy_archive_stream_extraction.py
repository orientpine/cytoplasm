"""`deploy_archive_stream` 소비자는 gzip 으로 풀어야 한다 — 회귀 고정.

2026-08-18 실측 배경: `deploy_archive_stream` 은 `tar -czf -` 로 **압축해서**
보내는데 `skills/mail/deploy.sh` 만 수신을 `tar -xf -` 로 하고 있었다. 그래서
vendored mailon 배포가

    tar: Archive is compressed. Use -z option
    tar: Error is not recoverable: exiting now

로 죽었다. 다른 소비자는 모두 `-xzf -` 였으니 이 한 곳만 어긋난 잠복 결함이고,
`set -e` 덕에 손상은 없었지만 그 배포 경로 자체가 막혀 있었다.

보내는 쪽이 한 곳(`automation/deploy_provenance.sh`)이므로 수신 규약도 한
곳에서 기계적으로 강제한다.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

# `tar ... -xf -` / `tar ... -xzf -` 처럼 **stdin 에서 푸는** 호출만 본다.
# 아카이브 파일을 인자로 받는 호출(`tar -xzf foo.tar.gz`)은 대상이 아니다.
_TAR_FROM_STDIN = re.compile(r"tar\b[^\n|;]*?-([a-zA-Z]*x[a-zA-Z]*)f\s+-")


def _shell_sources() -> list[Path]:
    return sorted(
        path
        for path in _REPO.rglob("*.sh")
        if ".git" not in path.parts and "node_modules" not in path.parts
    )


def _consumers() -> list[Path]:
    """`deploy_archive_stream` 를 호출하는 스크립트(정의 파일 자체는 제외)."""
    return [
        path
        for path in _shell_sources()
        if "deploy_archive_stream" in path.read_text(encoding="utf-8")
        and path.name != "deploy_provenance.sh"
    ]


def test_stream_producer_still_compresses() -> None:
    # Given: 수신 규약의 근거는 송신 쪽이 gzip 이라는 사실이다.
    source = (_REPO / "automation" / "deploy_provenance.sh").read_text(encoding="utf-8")

    # Then: 송신이 비압축으로 바뀌면 이 테스트가 먼저 깨져 규약을 다시 보게 한다.
    assert "-czf -" in source


def test_every_stream_consumer_extracts_with_gzip() -> None:
    # Given: 스트림을 소비하는 모든 배포 스크립트.
    consumers = _consumers()
    assert consumers, "deploy_archive_stream 소비자를 하나도 못 찾았다 — 스캔이 깨졌다"

    # When: stdin 에서 푸는 tar 호출의 플래그를 모은다.
    offenders: list[str] = []
    for path in consumers:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for flags in _TAR_FROM_STDIN.findall(line):
                if "z" not in flags:
                    offenders.append(f"{path.relative_to(_REPO)}:{line_number} (-{flags}f -)")

    # Then: 압축된 스트림을 비압축으로 풀려는 곳이 없다.
    assert not offenders, (
        "deploy_archive_stream 은 gzip 으로 보내므로 수신도 -z 가 필요하다:\n"
        + "\n".join(offenders)
    )
