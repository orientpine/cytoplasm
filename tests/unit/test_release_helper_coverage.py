"""설치기가 libexec 에 놓는 자산은 전부 드리프트 프로브가 봐야 한다.

2026-08-20 실측: 프로브는 8개 helper 자산 중 2개만 보고 있었고, 나머지 6개 중
**5개가 드리프트·부재** 상태였다 — 리컨실러 본체(`autophagy-converge-origin-main`)와
그것이 쓰는 `autophagy-converge.d/release_store.py`(08-02, prune 코드 이전)가 포함된다.
그래서 매 2분 수렴이 세대 정리를 건너뛰었고, 그 사실을 말해준 것은 드리프트 프로브가
아니라 한계를 넘긴 `release_store_usage` 였다 — 증상이 원인보다 먼저 보였다.

프로브의 배열은 사람이 손으로 유지하므로 자산이 늘면 조용히 빠진다. 이 테스트가
`assets.py`(설치 목록의 단일 진실)에서 기대치를 유도해 그 침묵을 깨뜨린다.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from automation.install.assets import build_inputs
from automation.node_config import load_node_config

_REPO = Path(__file__).resolve().parents[2]
_PROBE = _REPO / "automation" / "release_helper_probe.sh"


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    material = len(algorithm).to_bytes(4, "big") + algorithm
    material += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(material).decode()} coverage-test"


def _installed_under_libexec() -> set[str]:
    config = load_node_config(_REPO / "configs" / "node.example.toml")
    inputs = build_inputs(_REPO, config, _public_key())
    return {
        str(spec.path.relative_to(config.libexec_dir))
        for spec in inputs.files
        if config.libexec_dir in spec.path.parents
    }


def _probe_covered() -> set[str]:
    text = _PROBE.read_text(encoding="utf-8")
    # `local name="${HEALTHCHECK_X:-<default>}"` 의 기본값을 그대로 쓴다 — 프로브가
    # 이름을 바꾸면 여기도 따라 바뀜도록, 경로를 이 파일에 베껴 적지 않는다.
    defaults = dict(
        re.findall(r'local (\w+)="\$\{HEALTHCHECK_[A-Z_]+:-([^}]+)\}"', text)
    )

    def expand(value: str) -> str:  # 기본값끼리 서로를 참조한다($helper → $libexec/…).
        for _ in range(len(defaults) + 1):
            for name, replacement in defaults.items():
                value = value.replace(f"${name}", replacement)
        return value

    prefix = f"{defaults.get('libexec', '/usr/local/libexec')}/"
    covered: set[str] = set()
    # 배열 리터럴 안에서만 찾는다 — 파일 전체를 훑으면 `${entry%%|*}` 같은
    # 코드 속 파이프까지 항목으로 잎혀 진짜 항목을 삼킨다(2026-08-20 실측).
    for block in re.findall(r"local -a \w+=\((.*?)\n\s*\)", text, re.DOTALL):
        for installed, _source in re.findall(r'"([^"|]+)\|([^"]+)"', block):
            resolved = expand(installed)
            if resolved.startswith(prefix):
                covered.add(resolved[len(prefix) :])
    return covered


def test_every_libexec_asset_the_installer_places_is_watched_for_drift() -> None:
    # Given: 설치기가 놓는 libexec 자산 전부와, 프로브가 실제로 대조하는 목록.
    expected = _installed_under_libexec()
    covered = _probe_covered()

    # Then: 설치기가 놓는데 아무도 안 보는 자산이 없어야 한다.
    assert expected, "설치기가 libexec 에 아무것도 놓지 않는다 — 유도가 깨졌다"
    assert not expected - covered, (
        "이 자산들은 root 가 설치하지만 어떤 프로브도 보지 않는다 — 낡아도 조용하다. "
        f"release_helper_probe.sh 의 배열에 추가하라: {sorted(expected - covered)}"
    )


#: `assets.py` 밖의 프로비저너가 놓는 libexec 자산 — 사유와 함께 적는다.
#: 사유 없는 항목을 여기 넣으면 아래 테스트가 무뎀해진다.
_INSTALLED_BY_OTHER_PROVISIONERS = {
    "autophagy-gateway-pair": "provision-release-store.sh 가 설치한다(assets.py 목록 밖)",
}


def test_the_probe_does_not_watch_paths_nobody_installs() -> None:
    """반대 방향 — 아무도 놓지 않는 파일을 기다리면 프로브가 영원히 red 다."""
    installed = _installed_under_libexec() | set(_INSTALLED_BY_OTHER_PROVISIONERS)
    covered = _probe_covered()

    assert not covered - installed, (
        "프로브가 어느 설치 경로에도 없는 경로를 대조한다 — 상시 실패의 원인이다: "
        f"{sorted(covered - installed)}"
    )


def test_every_named_provisioner_exists() -> None:
    """항목이 가리키는 스크립트가 실제로 있어야 한다 — 오타는 재실행 안내를 죽인다."""
    text = _PROBE.read_text(encoding="utf-8")
    named = {
        entry.split("|")[2]
        for block in re.findall(r"local -a \w+=\((.*?)\n\s*\)", text, re.DOTALL)
        for entry in re.findall(r'"([^"]+\|[^"]+\|[^"]+)"', block)
    }

    assert named, "항목에서 프로비저너를 뽑지 못했다 — 형식이 바뀌었다"
    missing = {name for name in named if not (_REPO / "automation" / name).is_file()}
    assert not missing, f"프로브가 없는 스크립트를 안내한다: {sorted(missing)}"
