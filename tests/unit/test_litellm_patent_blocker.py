"""Unit tests for the LiteLLM patent-sensitive glm-main pre-call guard.

Loads configs/litellm-staging/custom_callbacks.py with stubbed fastapi/litellm
modules (this repo is stdlib-only) and exercises the tag + sentinel rejection
paths that close the GLM-fallback window for recall-released patent content.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_CALLBACKS_PATH = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "litellm-staging"
    / "custom_callbacks.py"
)


class _FakeHTTPException(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _load_callbacks(monkeypatch: pytest.MonkeyPatch):
    fastapi = types.ModuleType("fastapi")
    fastapi.HTTPException = _FakeHTTPException
    litellm = types.ModuleType("litellm")
    integrations = types.ModuleType("litellm.integrations")
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        pass

    custom_logger.CustomLogger = CustomLogger
    monkeypatch.setitem(sys.modules, "fastapi", fastapi)
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    monkeypatch.setitem(sys.modules, "litellm.integrations", integrations)
    monkeypatch.setitem(
        sys.modules, "litellm.integrations.custom_logger", custom_logger
    )
    spec = importlib.util.spec_from_file_location(
        "custom_callbacks_under_test", _CALLBACKS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hook(module, data):
    return asyncio.run(
        module.proxy_handler_instance.async_pre_call_hook(None, None, data, "completion")
    )


def test_glm_request_with_sentinel_in_string_content_rejected(monkeypatch) -> None:
    module = _load_callbacks(monkeypatch)
    data = {
        "model": "glm-main",
        "messages": [
            {"role": "user", "content": f"요약해줘: {module.PATENT_SENTINEL} 특허 원문"}
        ],
    }
    with pytest.raises(_FakeHTTPException) as excinfo:
        _hook(module, data)
    assert excinfo.value.status_code == 403
    assert "no_deployments_with_tag_routing" in excinfo.value.detail


def test_glm_request_with_sentinel_in_content_parts_rejected(monkeypatch) -> None:
    module = _load_callbacks(monkeypatch)
    data = {
        "model": "glm-main",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{module.PATENT_SENTINEL} 민감 본문"}
                ],
            }
        ],
    }
    with pytest.raises(_FakeHTTPException):
        _hook(module, data)


def test_glm_request_without_sentinel_passes(monkeypatch) -> None:
    module = _load_callbacks(monkeypatch)
    data = {
        "model": "glm-main",
        "messages": [{"role": "user", "content": "일반 질문입니다"}],
    }
    assert _hook(module, data) is data


def test_non_glm_request_with_sentinel_passes(monkeypatch) -> None:
    module = _load_callbacks(monkeypatch)
    data = {
        "model": "gpt-5.6-sol",
        "messages": [
            {"role": "user", "content": f"{module.PATENT_SENTINEL} 특허 검토"}
        ],
    }
    assert _hook(module, data) is data


def test_glm_request_with_patent_tag_still_rejected(monkeypatch) -> None:
    """Regression: the original W1-1 tag-based rejection must survive v2."""
    module = _load_callbacks(monkeypatch)
    data = {
        "model": "glm-main",
        "metadata": {"tags": ["patent-sensitive"]},
        "messages": [{"role": "user", "content": "태그 기반 요청"}],
    }
    with pytest.raises(_FakeHTTPException) as excinfo:
        _hook(module, data)
    assert excinfo.value.status_code == 403


def test_malformed_messages_do_not_crash_guard(monkeypatch) -> None:
    module = _load_callbacks(monkeypatch)
    data = {
        "model": "glm-main",
        "messages": [None, "raw-string", {"content": None}, {"content": [None, {}]}],
    }
    assert _hook(module, data) is data
