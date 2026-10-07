"""Where the external-effect gate reads its rule file from (todo 21).

The gateway imports the plugin body from a pinned release generation. The rule
file must come from that same generation so the gated agent cannot edit its own
rules; a copy of the module that has no ``configs/`` sibling falls back to the
legacy home copy, and an explicit environment override always wins.
"""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

from automation.interop import hermes_plugin, policy_paths

_REPO = Path(__file__).resolve().parents[2]
_SOURCE = _REPO / "automation" / "interop" / "policy_paths.py"
_RULES = "external-effect-tools.yaml"


def _load_copy(generation: Path, name: str) -> ModuleType:
    target = generation / "automation" / "interop" / "policy_paths.py"
    target.parent.mkdir(parents=True)
    shutil.copyfile(_SOURCE, target)
    spec = importlib.util.spec_from_file_location(name, target)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _home_rules(home: Path) -> Path:
    path = home / ".hermes" / "interop" / _RULES
    path.parent.mkdir(parents=True)
    path.write_text("tools: []\n", encoding="utf-8")
    return path


def test_release_tree_rules_are_preferred_when_the_module_runs_from_a_tree(tmp_path: Path) -> None:
    generation = tmp_path / "releases" / "gen-a"
    rules = generation / "configs" / _RULES
    rules.parent.mkdir(parents=True)
    rules.write_text("tools: []\n", encoding="utf-8")
    home = tmp_path / "home"
    _home_rules(home)
    module = _load_copy(generation, "policy_paths_release_copy")

    chosen = module.denylist_path({"HOME": str(home)})

    assert chosen == rules.resolve()


def test_the_repository_module_resolves_the_tracked_rule_file(tmp_path: Path) -> None:
    chosen = policy_paths.denylist_path({"HOME": str(tmp_path)})

    assert chosen == _REPO / "configs" / _RULES


def test_the_plugin_gate_uses_the_policy_path() -> None:
    assert hermes_plugin.EXTERNAL_EFFECT_DENYLIST == policy_paths.denylist_path()


def test_env_override_wins(tmp_path: Path) -> None:
    override = tmp_path / "elsewhere.yaml"
    env = {"EXTERNAL_EFFECT_DENYLIST_PATH": str(override), "HOME": str(tmp_path)}

    assert policy_paths.denylist_path(env) == override


def test_a_copy_without_a_configs_sibling_falls_back_to_the_home_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    expected = _home_rules(home)
    module = _load_copy(tmp_path / "plugins" / "interop-protocol", "policy_paths_bare_copy")

    assert module.denylist_path({"HOME": str(home)}) == expected


def test_a_missing_home_file_is_still_the_answer_so_the_gate_fails_closed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    module = _load_copy(tmp_path / "bare", "policy_paths_missing_copy")

    chosen = module.denylist_path({"HOME": str(home)})

    assert chosen == home / ".hermes" / "interop" / _RULES
    assert not chosen.exists()
