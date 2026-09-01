"""시드 config 은 Discord 표면의 tool-progress 를 꺼 둔다.

WHY: 벤더 게이트웨이는 도구 실행 진행 라인(``┊ 🔎 grep …``, ``┊ 📖 read …``,
``┊ 💻 $ …``, ``(×N)`` 누적)을 턴이 들어온 표면에 그대로 렌더한다
(``agent/display.py`` 의 프리뷰를 ``gateway/run.py`` 가 progress 버블로 편집).
공개 표면은 ``discord-public-message-policy`` 가 막지만 **1:1 DM 은 설계상
통과**라, 소유자가 보는 대화가 곧 내부 작업 로그였다 — 2026-08-22 소유자 요청은
"의미 있는 문장만 남기고 단순 실행 라인은 감춰라"였다.

고정 대상은 산문이 아니라 **게이트웨이가 실제로 읽는 값**이다.
``gateway/display_config.py:resolve_display_setting`` 이 1순위로 보는 경로가
``display.platforms.<platform>.<setting>`` 이므로, 실제 bash 로 렌더한 시드를
YAML 로 파싱해 그 경로의 값을 확인한다.

여기서 고정하는 것은 신규 설치의 시드다. 기존 노드는
``ensure_file_if_absent``(only-if-unset) 때문에 이 시드가 덮지 않으므로 라이브
config 적용은 별도 조치다(「추적 config = 불변 시드」).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "automation" / "provision-agent.sh"

_RENDER_ENV = {
    "LITELLM_MODEL": "model-under-test",
    "LITELLM_BASE_URL": "http://litellm.invalid/v1",
    "LITELLM_KEY_ENV": "LITELLM_KEY_UNDER_TEST",
    "NODE_AGENT_ACCOUNT": "agent",
    "FALLBACK_PROVIDER": "provider-under-test",
    "FALLBACK_MODEL": "fallback-under-test",
}


def _function_body(name: str) -> str:
    text = _SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}", text, re.S | re.M)
    assert match is not None, f"provision-agent.sh must define {name}()"
    return match.group(1)


def _rendered_config(account: str) -> dict[str, Any]:
    """실제 bash 로 시드를 렌더하고 YAML 로 파싱해 돌려준다."""
    rendered = subprocess.run(
        ["bash", "-c", _function_body("render_config")],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **_RENDER_ENV, "ACCOUNT": account},
    ).stdout
    parsed = yaml.safe_load(rendered)
    assert isinstance(parsed, dict), "시드 config 은 YAML 매핑이어야 한다"
    return parsed


@pytest.mark.parametrize("account", ["agent", "peer"])
def test_seed_disables_tool_progress_on_discord(account: str) -> None:
    """게이트웨이가 읽는 바로 그 경로에서 ``off`` 가 나와야 한다.

    문자열 ``"off"`` 로 인용하는 것은 의도적이다 — YAML 1.1 의 bare ``off`` 는
    boolean False 로 파싱돼 ``hermes config get`` 에 ``False`` 로 보이고,
    운영자가 "무엇이 꺼졌는지"를 읽을 수 없게 된다(게이트웨이 자체는 양쪽을
    같은 값으로 정규화한다).
    """
    display = _rendered_config(account)["display"]
    assert display["platforms"]["discord"]["tool_progress"] == "off"


@pytest.mark.parametrize("account", ["agent", "peer"])
def test_seed_scopes_the_override_to_discord_tool_progress(account: str) -> None:
    """폭발 반경 고정: 전역 키도, 다른 표면도 건드리지 않는다.

    ``display.tool_progress`` 를 전역으로 끄면 운영자가 쓰는 CLI(terminal) 뷰까지
    함께 죽고, 같은 platforms 블록에 다른 키를 얹으면 의미 있는 문장(interim
    assistant 메시지·최종 응답)까지 사라진다 — 소유자가 요청한 것은 실행 라인
    억제뿐이다.
    """
    display = _rendered_config(account)["display"]
    assert "tool_progress" not in display, "전역 tool_progress 는 시드가 정하지 않는다"
    assert set(display["platforms"]) == {"discord"}
    assert set(display["platforms"]["discord"]) == {"tool_progress"}


@pytest.mark.parametrize("account", ["agent", "peer"])
def test_seed_starts_hourly_approval_reminders_after_one_hour(account: str) -> None:
    """Provisioning must preserve the owner's one-hour reminder policy."""
    reminders = _rendered_config(account)["approval_reminders"]

    assert reminders == {
        "enabled": True,
        "initial_delay": "1h",
        "repeat_interval": "1h",
    }
