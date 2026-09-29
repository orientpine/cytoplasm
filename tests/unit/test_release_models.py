"""The release-applied notice names the models every account runs on.

Owner instruction 2026-09-29: the model pair should be visible on every release in
#notifications, not only when the owner runs a command. Missing facts still let the notice go
out, saying so; they never block it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation import owner_notice, release_applied_notice, release_models

_HEAD = "b" * 40
_AGENT = """model:
  default: gpt-6-sol
  provider: openai-codex
agent:
  max_turns: 500
  verbose: false
fallback_providers:
  - provider: xai-oauth
    model: grok-4.7
"""


def test_an_absent_max_turns_is_the_hermes_default() -> None:
    peer = _AGENT.replace("agent:\n  max_turns: 500\n  verbose: false\n", "agent: {}\n")

    assert release_models.summarize(peer) == release_models.summarize(_AGENT) == {
        "main": "openai-codex/gpt-6-sol",
        "fallback": "xai-oauth/grok-4.7",
        "max_turns": "500",
    }


def test_matching_accounts_render_one_line_and_drift_names_the_fix() -> None:
    same = release_models.parse_output(f"=== agent\n{_AGENT}=== peer\n{_AGENT}")
    drift = {**same, "peer": {**same["peer"], "main": "custom:litellm/glm-main"}}

    assert release_models.render(json.dumps(same)) == (
        "모델: 주 openai-codex/gpt-6-sol · 폴백 xai-oauth/grok-4.7 · 반복 한도 500 (agent·peer 같음)"
    )
    assert "python3 -m automation.model_sync --apply" in release_models.render(json.dumps(drift))
    assert release_models.render("not json").startswith("모델: 확인 못 함")


def test_the_node_send_carries_the_model_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer = tmp_path / "current"
    pointer.symlink_to(tmp_path / _HEAD)
    monkeypatch.setenv("NODE_RELEASE_CURRENT", str(pointer))
    sent: list[str] = []
    monkeypatch.setattr(
        owner_notice, "notify_owner", lambda content, *, message=None: sent.append(content) or True
    )
    summaries = release_models.parse_output(f"=== agent\n{_AGENT}=== peer\n{_AGENT}")

    code = release_applied_notice.main(
        ["send", "--version", "v1.2.4", "--head", _HEAD, "--models", json.dumps(summaries)]
    )

    assert code == 0
    assert sent[0].splitlines()[1].startswith("모델: 주 openai-codex/gpt-6-sol")
