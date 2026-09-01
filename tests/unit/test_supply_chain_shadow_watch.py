"""SC-1 shadow tick: name-only detection, once-per-new-shadow notice, at-least-once retry.

The daily selfskill audit keeps its SHADOWS-GOVERNED line; this tick only shrinks the
exposure window from a day to two minutes. What must hold: the cheap check judges with
the SAME walk as the audit (bundled excluded, depth 1-2), a new shadow is announced
exactly once, a failed delivery retries, and a resolved-then-recreated shadow re-alerts.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Final

import pytest

from automation.selfskill_audit.scan import shadowed_skill_names
from automation.supply_chain_shadow_watch import plan_shadow_notice, run_shadow_check

_REPO: Final = Path(__file__).resolve().parents[2]


class _Notify:
    def __init__(self, *, fails: int = 0) -> None:
        self.sent: list[str] = []
        self._fails = fails

    def __call__(self, text: str) -> bool:
        if self._fails > 0:
            self._fails -= 1
            return False
        self.sent.append(text)
        return True


def _skills_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    root = home / ".hermes" / "skills"
    (root / "recall").mkdir(parents=True)
    _ = (root / "recall" / "SKILL.md").write_text("---\nname: recall\n---\n", encoding="utf-8")
    (root / "software-development" / "mail").mkdir(parents=True)
    _ = (root / "software-development" / "mail" / "SKILL.md").write_text(
        "---\nname: mail\n---\n", encoding="utf-8"
    )
    (root / "helper").mkdir()
    _ = (root / "helper" / "SKILL.md").write_text("---\nname: helper\n---\n", encoding="utf-8")
    return home


def _governed(tmp_path: Path, *names: str) -> Path:
    live = tmp_path / "live"
    live.mkdir(exist_ok=True)
    for name in names:
        (live / name).mkdir(exist_ok=True)
    return live


def test_name_compare_finds_top_level_and_nested_shadows(tmp_path: Path) -> None:
    home = _skills_home(tmp_path)
    live = _governed(tmp_path, "recall", "mail", "wiki")

    assert shadowed_skill_names(home, live) == ("mail", "recall")


def test_bundled_skills_never_count_as_shadows(tmp_path: Path) -> None:
    home = _skills_home(tmp_path)
    _ = (home / ".hermes" / "skills" / ".bundled_manifest").write_text(
        "recall: bundled\n", encoding="utf-8"
    )
    live = _governed(tmp_path, "recall", "mail")

    assert shadowed_skill_names(home, live) == ("mail",)


def test_missing_roots_mean_no_shadows(tmp_path: Path) -> None:
    assert shadowed_skill_names(tmp_path / "absent", _governed(tmp_path, "recall")) == ()
    assert shadowed_skill_names(_skills_home(tmp_path), None) == ()


def test_a_new_shadow_is_announced_and_an_unchanged_one_is_not() -> None:
    first = plan_shadow_notice(("recall",), ())
    unchanged = plan_shadow_notice(("recall",), ("recall",))
    cleared = plan_shadow_notice((), ("recall",))

    assert first.notice is not None
    assert "recall" in first.notice
    assert "archive" in first.notice
    assert first.state == ("recall",)
    assert unchanged.notice is None
    assert unchanged.state == ("recall",)
    assert cleared.notice is None
    assert cleared.state == ()


def test_a_vanished_name_is_pruned_so_recurrence_realerts() -> None:
    pruned = plan_shadow_notice(("mail",), ("mail", "recall"))

    assert pruned.notice is None
    assert pruned.state == ("mail",)
    assert plan_shadow_notice(("mail", "recall"), pruned.state).notice is not None


def _tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    live: Path,
    notify: _Notify,
) -> tuple[str, ...]:
    monkeypatch.setenv("SUPPLY_CHAIN_SHADOW_STATE", str(tmp_path / "shadows.json"))
    monkeypatch.delenv("AUTOPHAGY_OWNER_ID", raising=False)
    return run_shadow_check(home=home, governed_root=live, notify=notify)


def test_run_notifies_once_then_stays_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notify = _Notify()
    home = _skills_home(tmp_path)
    live = _governed(tmp_path, "recall")

    first = _tick(tmp_path, monkeypatch, home, live, notify)
    second = _tick(tmp_path, monkeypatch, home, live, notify)

    assert first == ("recall",)
    assert second == ("recall",)
    assert len(notify.sent) == 1
    assert "SHADOWS-GOVERNED recall" in notify.sent[0]


def test_a_failed_delivery_is_retried_on_the_next_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notify = _Notify(fails=1)
    home = _skills_home(tmp_path)
    live = _governed(tmp_path, "recall")

    _ = _tick(tmp_path, monkeypatch, home, live, notify)
    _ = _tick(tmp_path, monkeypatch, home, live, notify)
    _ = _tick(tmp_path, monkeypatch, home, live, notify)

    assert len(notify.sent) == 1  # 실패 1회 뒤 정확히 한 번, 그 뒤 침묵


def test_resolution_clears_state_and_recurrence_realerts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notify = _Notify()
    home = _skills_home(tmp_path)
    live = _governed(tmp_path, "recall")

    _ = _tick(tmp_path, monkeypatch, home, live, notify)
    shutil.rmtree(home / ".hermes" / "skills" / "recall")
    cleared = _tick(tmp_path, monkeypatch, home, live, notify)
    (home / ".hermes" / "skills" / "recall").mkdir()
    _ = (home / ".hermes" / "skills" / "recall" / "SKILL.md").write_text(
        "---\nname: recall\n---\n", encoding="utf-8"
    )
    recurred = _tick(tmp_path, monkeypatch, home, live, notify)

    assert cleared == ()
    assert recurred == ("recall",)
    assert len(notify.sent) == 2


def test_the_two_minute_tick_wires_the_shadow_check_fail_soft() -> None:
    """탐지 실패가 승인 재개 틱을 세우면 안 된다 — 배선은 tick 요약 이후, try 아래."""
    source = (_REPO / "automation" / "supply_chain_watch_cli.py").read_text(encoding="utf-8")

    assert "run_shadow_check()" in source
    assert "shadow-check-error" in source
    assert source.index("write_tick_summary(") < source.index("run_shadow_check()")
