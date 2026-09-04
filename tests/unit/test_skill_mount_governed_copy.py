"""governed 사본 판정을 스킬마다 베끼지 않도록 단일 정의를 고정한다."""
from __future__ import annotations

from pathlib import Path

import pytest

from skills.mail.scripts import mail_runtime
from automation.skill_mount import (
    LIVE_ROOT_ENV,
    STALE_COPY_MARKER,
    governed_copy_refusal,
)


def _fake_store(tmp_path: Path, skill: str = "mail") -> tuple[Path, Path]:
    """실제 live/releases 심링크 배포 모양과 스크립트를 만든다."""
    release = tmp_path / "srv" / "autophagy-skills" / "releases" / skill / ("a" * 8)
    scripts = release / "scripts"
    scripts.mkdir(parents=True)
    _ = (scripts / "cli.py").write_text("# governed copy\n", encoding="utf-8")
    live = tmp_path / "srv" / "autophagy-skills" / "live"
    live.mkdir(parents=True)
    (live / skill).symlink_to(release, target_is_directory=True)
    return live, scripts


def _stale_copy(tmp_path: Path) -> Path:
    script = tmp_path / "mirror" / "skills" / "mail" / "scripts" / "cli.py"
    script.parent.mkdir(parents=True)
    _ = script.write_text("# stale copy\n", encoding="utf-8")
    return script


def test_canonical_guard_refuses_a_copy_outside_the_governed_mount(tmp_path: Path) -> None:
    live, _scripts = _fake_store(tmp_path)
    stale = _stale_copy(tmp_path)

    refusal = governed_copy_refusal("mail", stale, env={LIVE_ROOT_ENV: str(live)})

    assert refusal is not None
    assert refusal.startswith(STALE_COPY_MARKER)
    assert str(live / "mail" / "scripts" / "cli.py") in refusal
    assert f"readlink {live / 'mail'}" in refusal


def test_canonical_guard_allows_live_and_release_paths(tmp_path: Path) -> None:
    live, scripts = _fake_store(tmp_path)
    env = {LIVE_ROOT_ENV: str(live)}

    assert governed_copy_refusal("mail", live / "mail" / "scripts" / "cli.py", env=env) is None
    assert governed_copy_refusal("mail", scripts / "cli.py", env=env) is None


def test_canonical_guard_allows_hosts_without_the_skill_mount(tmp_path: Path) -> None:
    stale = _stale_copy(tmp_path)

    assert governed_copy_refusal("mail", stale, env={LIVE_ROOT_ENV: str(tmp_path / "absent")}) is None


def test_canonical_guard_refuses_when_resolution_cannot_prove_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live, _scripts = _fake_store(tmp_path)
    stale = _stale_copy(tmp_path)
    original_resolve = Path.resolve

    def unreadable_resolve(path: Path, *, strict: bool = False) -> Path:
        if path == live / "mail" / "scripts":
            raise OSError("unreadable mount")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", unreadable_resolve)

    refusal = governed_copy_refusal("mail", stale, env={LIVE_ROOT_ENV: str(live)})

    assert refusal is not None
    assert refusal.startswith(STALE_COPY_MARKER)
    assert "OSError" in refusal


def test_mail_guard_delegates_to_the_canonical_judgment(tmp_path: Path) -> None:
    live, _scripts = _fake_store(tmp_path)
    stale = _stale_copy(tmp_path)
    env = {LIVE_ROOT_ENV: str(live)}

    assert mail_runtime.governed_copy_refusal(stale, env=env) == governed_copy_refusal(
        "mail", stale, env=env
    )
