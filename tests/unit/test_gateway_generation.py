"""Gateway generation record and its check (todo 21).

The interop plugin records, from inside the gateway process only, which
release generation it was imported from and the digest of the installed
plugin file. ``check`` compares that record with the live pidfile and the
release a deploy expects; anything it cannot read is ``unknown``, never ``ok``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation import gateway_generation

_REPO = Path(__file__).resolve().parents[2]
_PLUGIN = "interop-protocol"
_DEAD_PID = 999_999_999


def _hermes(home: Path) -> Path:
    path = home / ".hermes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_pidfile(home: Path, pid: int) -> None:
    payload = {"pid": pid, "kind": "hermes-gateway", "argv": ["hermes", "gateway", "run"]}
    (_hermes(home) / "gateway.pid").write_text(json.dumps(payload), encoding="utf-8")


def _plugin_file(home: Path, body: str = "register = None\n") -> Path:
    path = _hermes(home) / "plugins" / _PLUGIN / "__init__.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _record_path(home: Path) -> Path:
    return home / ".hermes" / "gateway-generation.json"


@pytest.fixture
def gateway_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    _hermes(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["/opt/hermes/hermes_cli/main.py", "gateway", "run"])
    monkeypatch.setenv("SYSTEMD_EXEC_PID", str(os.getpid()))
    _write_pidfile(home, os.getpid())
    return home


def _check(home: Path, runtime_root: Path = _REPO) -> tuple[str, str, str, str]:
    return gateway_generation.check(home, runtime_root, [_PLUGIN])


def test_record_then_check_is_ok(gateway_home: Path) -> None:
    gateway_generation.record(_PLUGIN, str(_plugin_file(gateway_home)))

    status, want, have, detail = _check(gateway_home)

    assert (status, detail) == ("ok", "-")
    assert want == have == Path(os.path.realpath(_REPO)).name
    assert _record_path(gateway_home).stat().st_mode & 0o777 == 0o600
    stored = json.loads(_record_path(gateway_home).read_text(encoding="utf-8"))
    assert stored["pid"] == os.getpid()
    assert stored["import_root"] == os.path.realpath(_REPO)


def test_non_gateway_process_never_writes(gateway_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["/opt/hermes/bin/hermes", "plugins", "list"])

    gateway_generation.record(_PLUGIN, str(_plugin_file(gateway_home)))

    assert not _record_path(gateway_home).exists()


def test_a_child_of_the_gateway_never_writes(gateway_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYSTEMD_EXEC_PID", str(os.getpid() + 1))

    gateway_generation.record(_PLUGIN, str(_plugin_file(gateway_home)))

    assert not _record_path(gateway_home).exists()


def test_record_never_raises_when_home_is_unwritable(gateway_home: Path) -> None:
    plugin = _plugin_file(gateway_home)
    hermes = gateway_home / ".hermes"
    hermes.chmod(0o500)
    try:
        gateway_generation.record(_PLUGIN, str(plugin))
        gateway_generation.record(_PLUGIN, str(gateway_home / "missing.py"))
    finally:
        hermes.chmod(0o700)

    assert not _record_path(gateway_home).exists()


def _stale_no_record(home: Path) -> Path:
    del home
    return _REPO


def _stale_pid(home: Path) -> Path:
    _write_pidfile(home, 1)
    return _REPO


def _stale_dead(home: Path) -> Path:
    stored = json.loads(_record_path(home).read_text(encoding="utf-8"))
    stored["pid"] = _DEAD_PID
    _record_path(home).write_text(json.dumps(stored), encoding="utf-8")
    _write_pidfile(home, _DEAD_PID)
    return _REPO


def _stale_import_root(home: Path) -> Path:
    other = home.parent / "other-generation"
    other.mkdir()
    return other


def _stale_plugin(home: Path) -> Path:
    _plugin_file(home, "register = 'changed'\n")
    return _REPO


@pytest.mark.parametrize(
    ("reason", "arrange"),
    [
        ("no-record", _stale_no_record),
        ("pid", _stale_pid),
        ("dead", _stale_dead),
        ("import-root", _stale_import_root),
        (f"plugin:{_PLUGIN}", _stale_plugin),
    ],
)
def test_check_reports_each_stale_reason(gateway_home: Path, reason: str, arrange) -> None:
    plugin = _plugin_file(gateway_home)
    if reason != "no-record":
        gateway_generation.record(_PLUGIN, str(plugin))

    runtime_root = arrange(gateway_home)

    status, _want, _have, detail = _check(gateway_home, runtime_root)
    assert (status, detail) == ("stale", reason)


def test_unreadable_pidfile_is_unknown_not_ok(gateway_home: Path) -> None:
    gateway_generation.record(_PLUGIN, str(_plugin_file(gateway_home)))
    pidfile = gateway_home / ".hermes" / "gateway.pid"
    observed: list[str] = []

    for body in ("not json", "[]", '{"pid": "12"}', '{"pid": true}', '{"pid": 0}'):
        pidfile.write_text(body, encoding="utf-8")
        observed.append(_check(gateway_home)[0])
    pidfile.chmod(0o000)
    try:
        observed.append(_check(gateway_home)[0])
    finally:
        pidfile.chmod(0o600)
    pidfile.unlink()
    observed.append(_check(gateway_home)[0])

    assert observed == ["unknown"] * 7


def test_a_corrupt_record_is_never_ok(gateway_home: Path) -> None:
    _plugin_file(gateway_home)
    statuses: list[str] = []

    for body in ("{", "[]", '{"pid": "x"}', json.dumps({"pid": os.getpid(), "import_root": 3, "plugins": {}})):
        _record_path(gateway_home).write_text(body, encoding="utf-8")
        statuses.append(_check(gateway_home)[0])

    assert "ok" not in statuses
    assert set(statuses) <= {"stale", "unknown"}


def test_a_record_from_a_previous_pid_starts_fresh(gateway_home: Path) -> None:
    previous = {"pid": _DEAD_PID, "import_root": "/old", "recorded_at": "-", "plugins": {"old-plugin": "0" * 64}}
    _record_path(gateway_home).write_text(json.dumps(previous), encoding="utf-8")

    gateway_generation.record(_PLUGIN, str(_plugin_file(gateway_home)))

    stored = json.loads(_record_path(gateway_home).read_text(encoding="utf-8"))
    assert stored["pid"] == os.getpid()
    assert set(stored["plugins"]) == {_PLUGIN}


def test_the_same_pid_merges_plugins(gateway_home: Path) -> None:
    gateway_generation.record(_PLUGIN, str(_plugin_file(gateway_home)))
    other = _hermes(gateway_home) / "plugins" / "other" / "__init__.py"
    other.parent.mkdir(parents=True)
    other.write_text("x = 1\n", encoding="utf-8")

    gateway_generation.record("other", str(other))

    stored = json.loads(_record_path(gateway_home).read_text(encoding="utf-8"))
    assert set(stored["plugins"]) == {_PLUGIN, "other"}


def test_a_failed_replace_keeps_the_previous_record(gateway_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway_generation.record(_PLUGIN, str(_plugin_file(gateway_home)))
    before = _record_path(gateway_home).read_bytes()

    def refuse(source: object, target: object) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(gateway_generation.os, "replace", refuse)
    gateway_generation.record("other", str(_plugin_file(gateway_home, "y = 2\n")))

    assert _record_path(gateway_home).read_bytes() == before
    assert sorted(path.name for path in (gateway_home / ".hermes").iterdir()) == [
        "gateway-generation.json", "gateway.pid", "plugins",
    ]


def _cli(home: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(_REPO)}
    return subprocess.run(
        [sys.executable, "-B", "-m", "automation.gateway_generation", "--check", "--runtime-root", str(_REPO), *extra],
        capture_output=True, text=True, env=env, cwd=_REPO, check=False,
    )


def test_the_cli_prints_one_line_and_maps_status_to_rc(gateway_home: Path) -> None:
    gateway_generation.record(_PLUGIN, str(_plugin_file(gateway_home)))

    ok = _cli(gateway_home, "--require", _PLUGIN)
    _plugin_file(gateway_home, "tampered = True\n")
    stale = _cli(gateway_home, "--require", _PLUGIN)
    (gateway_home / ".hermes" / "gateway.pid").unlink()
    unknown = _cli(gateway_home, "--require", _PLUGIN)

    assert [ok.returncode, stale.returncode, unknown.returncode] == [0, 1, 2]
    assert [run.stdout.split()[:2] for run in (ok, stale, unknown)] == [
        ["GATEWAY-GENERATION", "ok"], ["GATEWAY-GENERATION", "stale"], ["GATEWAY-GENERATION", "unknown"],
    ]
    assert all(len(run.stdout.splitlines()) == 1 for run in (ok, stale, unknown))
    assert f"detail=plugin:{_PLUGIN}" in stale.stdout.split()
