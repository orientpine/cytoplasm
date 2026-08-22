"""릴리스 밖에 root 로 설치되는 자산이 드리프트하면 프로브가 말해야 한다.

2분 리컨실러는 **릴리스 트리만** 수렴시킨다. systemd 유닛·sudoers·libexec 헬퍼는
프로비저너가 root 로 설치하므로, 그것들이 바뀐 커밋이 머지돼도 노드에는 아무 일도
일어나지 않는다 — 그리고 2026-08-19 까지 그 사실을 말해 주는 것이 아무것도 없었다.

그 공백의 대가는 3일이었다: `autophagy-deploy-reconcile.service` 에 `BindPaths=/run/user`
가 없어 모든 수렴이 게이트웨이 재시작에서 롤백됐는데, 유일한 흔적은 상태 파일 안의
`reason=gateway-restart` 뿐이었다. 기존 `probe_release_helper_drift` 는 libexec 의
`autophagy-install-release` 하나만 보고 있었다.

그래서 "프로브가 프로비저너와 같은 자산 집합을 본다"를 여기서 기계적으로 고정한다 —
프로비저너에 설치 대상이 추가되면 이 테스트가 먼저 깨진다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_PROBE: Final = _REPO / "automation" / "release_helper_probe.sh"
_PROVISIONER: Final = _REPO / "automation" / "provision-deploy-reconcile.sh"

#: 0440 root:root 이라 cron 계정이 읽을 수 없다. 프로비저너가 설치 시점에
#: `sudo -n -l -U <account>` 로 실효성을 이미 증명하므로 프로브는 건너뛴다.
_UNREADABLE_BY_DESIGN: Final = "sudoers"


def _probe_text() -> str:
    return _PROBE.read_text(encoding="utf-8")


def test_probe_watches_the_reconcile_unit_that_cost_three_days() -> None:
    text = _probe_text()
    assert "autophagy-deploy-reconcile.service" in text, (
        "이 유닛의 드리프트가 2026-08-19 사건의 원인이었다 — 프로브가 보지 않으면 반복된다"
    )
    assert "autophagy-deploy-reconcile.timer" in text


def test_probe_watches_the_gateway_helper_the_unit_escalates_to() -> None:
    """재시작을 실제로 수행하는 것은 이 헬퍼다 — 유닛만 봐서는 절반이다."""
    assert "autophagy-gateway-pair" in _probe_text()


def test_templated_assets_are_rendered_before_comparison() -> None:
    """`.service` 와 `gateway_pair.py` 는 `$NODE_*` 템플릿이다.

    원문끼리 비교하면 **모든 노드가 항상 드리프트로 보인다** — 그러면 프로브는 상시
    red 가 되고, 상시 red 는 신호가 아니다(2026-08-04 G8 선례와 같은 실패 방식).
    """
    text = _probe_text()
    assert "node_asset_renderer.py" in text
    assert "PYTHONDONTWRITEBYTECODE" in text, (
        "봉인된 릴리스 트리에서 python 을 돌린다 — __pycache__ 하나가 이후 모든 배포를 막는다"
    )


def test_the_sudoers_exclusion_is_documented_not_forgotten() -> None:
    probe = _probe_text()
    assert _UNREADABLE_BY_DESIGN in probe, "왜 빠졌는지 적혀 있지 않으면 다음 사람이 결함으로 읽는다"


def test_every_installed_target_is_either_watched_or_documented() -> None:
    """프로비저너가 설치하는 것과 프로브가 보는 것이 어긋나지 않게 고정한다."""
    installed = set(re.findall(r"install -m \d+ -o root -g root [^\n]*", _PROVISIONER.read_text(encoding="utf-8")))
    assert installed, "프로비저너에서 install 줄을 찾지 못했다 — 파싱이 낡았다"
    probe = _probe_text()
    for line in installed:
        if "sudoers" in line:
            continue  # 위 상수의 사유대로 의도적 제외
        target = line.rsplit("/", 1)[-1].strip('"')
        assert target in probe, f"프로비저너가 설치하는 {target} 를 프로브가 보지 않는다"


def test_probe_exports_the_same_read_only_check_for_land() -> None:
    probe = _probe_text()
    assert "release_helper_probe_script" in probe
    assert "probe_release_helper_drift node account target" in probe
