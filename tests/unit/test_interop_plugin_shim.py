"""The thin interop plugin entry point installed in the account home (todo 21).

Hermes loads ``~/.hermes/plugins/interop-protocol/__init__.py`` by path and
calls its ``register`` attribute. The shim must always register the release
plugin's gate hooks; recording the gateway generation is best effort, so a
missing generation module still registers the gate and surfaces as stale.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

import automation
from automation import gateway_generation

_REPO = Path(__file__).resolve().parents[2]
_SHIM = _REPO / "automation" / "interop" / "plugin_shim" / "__init__.py"
_MANIFEST = _REPO / "automation" / "interop" / "hermes_plugin" / "plugin.yaml"
_PLUGIN = "interop-protocol"


class FakeContext:
    def __init__(self) -> None:
        self.hooks: dict[str, object] = {}

    def register_hook(self, name: str, callback: object) -> None:
        self.hooks[name] = callback


def _declared_hooks() -> set[str]:
    lines = _MANIFEST.read_text(encoding="utf-8").splitlines()
    return {line.strip()[2:] for line in lines if line.startswith("  - ")}


def _install(home: Path) -> Path:
    target = home / ".hermes" / "plugins" / _PLUGIN / "__init__.py"
    target.parent.mkdir(parents=True)
    shutil.copyfile(_SHIM, target)
    return target


def _load_like_hermes(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gateway_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["/opt/hermes/hermes_cli/main.py", "gateway", "run"])
    monkeypatch.setenv("SYSTEMD_EXEC_PID", str(os.getpid()))
    pidfile = {"pid": os.getpid(), "kind": "hermes-gateway", "argv": ["hermes", "gateway", "run"]}
    (home / ".hermes" / "gateway.pid").write_text(json.dumps(pidfile), encoding="utf-8")
    return home


def test_shim_registers_the_release_plugin_and_records(gateway_home: Path) -> None:
    installed = _install(gateway_home)
    module = _load_like_hermes(installed, "hermes_plugins.interop_protocol_happy")
    context = FakeContext()

    getattr(module, "register")(context)

    assert set(context.hooks) == _declared_hooks()
    assert len(context.hooks) == 6
    assert gateway_generation.check(gateway_home, _REPO, [_PLUGIN])[0] == "ok"


def test_the_shim_registers_the_gate_without_the_generation_module(
    gateway_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = _install(gateway_home)
    monkeypatch.delattr(automation, "gateway_generation", raising=False)
    monkeypatch.setitem(sys.modules, "automation.gateway_generation", None)
    module = _load_like_hermes(installed, "hermes_plugins.interop_protocol_legacy")
    context = FakeContext()

    getattr(module, "register")(context)

    assert set(context.hooks) == _declared_hooks()
    assert not (gateway_home / ".hermes" / "gateway-generation.json").exists()
    assert gateway_generation.check(gateway_home, _REPO, [_PLUGIN])[::3] == ("stale", "no-record")


def test_the_gate_hook_is_the_release_plugin_function(gateway_home: Path) -> None:
    from automation.interop import hermes_plugin

    module = _load_like_hermes(_install(gateway_home), "hermes_plugins.interop_protocol_identity")
    context = FakeContext()

    getattr(module, "register")(context)

    assert context.hooks["pre_tool_call"] is hermes_plugin.pre_tool_call
