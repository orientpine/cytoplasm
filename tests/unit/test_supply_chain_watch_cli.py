"""The watcher's node wiring, and the one systemd option that would silently disable it.

FA-3. Everything the watcher decides is already assembled and tested; this is the edge
that talks to the node. Two things are worth pinning here, and neither is about the
happy path.

**``ProtectHome=yes`` must not be set.** All three things this watcher reads live under
``$HOME`` of the account that owns the gate records: the records themselves
(``~/.hermes/skill-gate``), the interop config that carries the owner id
(``~/.hermes/interop/config.json``), and the bot token (``~/.env.secrets``, turned into
an EnvironmentFile drop-in by ``provision-agent.sh``). That option replaces ``/home``
with an empty directory, so setting it means the owner id lookup exits 2 — and if it
somehow survived, enumeration would return zero records every tick, forever, silently.
The neighbouring repair unit sets it safely only because its state is all under ``/srv``.

**The identity comes from the gate, not from here.** ``skill_gate._identity()`` is the
canonical factory and its directory is "the ONLY resolver of a surface". A CLI that
assembled its own would be a second construction site for the thing that decides which
channel an approval lives on — and second copies are the ones that rot.
"""
from __future__ import annotations

from pathlib import Path
import json

from automation.supply_chain_plan import PendingRequest
from automation.supply_chain_records import EnumerationResult
from automation.supply_chain_watch import FailureAttempt, TickResult
from automation.supply_chain_watch_cli import (
    RECORD_OWNER,
    load_failures,
    run_command,
    state_path,
    write_tick_summary,
)
from automation import supply_chain_watch_cli
from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config

_REPO = Path(__file__).resolve().parents[2]
_SERVICE = _REPO / "automation" / "systemd" / "autophagy-supply-chain-watch.service"
_TIMER = _REPO / "automation" / "systemd" / "autophagy-supply-chain-watch.timer"
_MODULE = _REPO / "automation" / "supply_chain_watch_cli.py"


def _service_text() -> str:
    return render_asset(_SERVICE, default_node_config())


def _directives(unit: Path) -> list[str]:
    """Only the settings systemd acts on — the comments explain the traps by name."""
    return [
        line.strip()
        for line in unit.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_protect_home_is_never_set() -> None:
    """It would remove the records, the owner id and the token in one line."""
    directives = _directives(_SERVICE)
    assert "ProtectHome=yes" not in directives
    assert "ProtectHome=no" in directives, "state it explicitly so nobody 'hardens' it later"


def test_the_unit_runs_as_the_account_that_owns_the_records() -> None:
    text = _service_text()
    assert f"User={RECORD_OWNER}" in text
    assert f"Group={RECORD_OWNER}" in text


def test_the_token_comes_from_that_account_secrets_file() -> None:
    """Same arrangement the report-hub collector already runs in production."""
    assert f"EnvironmentFile=/home/{RECORD_OWNER}/.env.secrets" in _service_text()


def test_the_unit_invokes_this_cli() -> None:
    assert "automation/supply_chain_watch_cli.py" in _SERVICE.read_text(encoding="utf-8")


def test_the_timer_ticks_and_catches_up_after_downtime() -> None:
    text = _TIMER.read_text(encoding="utf-8")
    assert "OnUnitActiveSec=2min" in text
    assert "Persistent=true" in text


def test_the_identity_is_not_rebuilt_here() -> None:
    """skill_gate._identity() is the canonical factory; a second one would drift."""
    text = _MODULE.read_text(encoding="utf-8")
    assert "_identity()" in text
    assert "DiscordChannelDirectory(" not in text
    assert "DISCORD_BOT_TOKEN" not in text, "the gate reads the token, not this module"


def test_the_runner_reports_the_exit_code_verbatim() -> None:
    """The outcome table reads this number; losing it would erase 8-vs-9."""
    assert run_command(("bash", "-c", "exit 9")) == 9
    assert run_command(("bash", "-c", "exit 0")) == 0


def test_the_runner_does_not_raise_on_a_missing_program() -> None:
    """A tick must survive a broken command and report it, not crash mid-directory."""
    assert run_command(("/nonexistent/deploy-skill.sh",)) != 0


def test_no_bypass_flag_appears_in_the_wiring() -> None:
    text = _MODULE.read_text(encoding="utf-8")
    for bypass in ("--sandbox-only", "--approve-only", "--request-only", "--fresh"):
        assert bypass not in text, bypass


def test_tick_summary_default_lives_outside_the_checkout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUPPLY_CHAIN_WATCH_STATE", raising=False)

    assert state_path() == tmp_path / ".hermes" / "supply-chain-watch" / "tick.json"


def test_tick_summary_is_atomic_private_and_machine_readable(tmp_path: Path) -> None:
    path = tmp_path / "state" / "tick.json"
    request = PendingRequest("skill-deploy:demo", "skill-deploy", "demo", "demo")
    results = (TickResult(request, "failed", "resume-exit:126"),)

    failures = {"skill-deploy:demo": FailureAttempt("abc:resume-exit:126", 2, 3700.0)}
    write_tick_summary(
        path,
        results,
        failures=failures,
        release_sha="abc",
        timestamp="2026-08-03T00:00:00Z",
    )

    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "failures": {
            "skill-deploy:demo": {
                "attempts": 2,
                "fingerprint": "abc:resume-exit:126",
                "next_attempt_at": 3700.0,
            }
        },
        "release_sha": "abc",
        "results": [
            {"key": "skill-deploy:demo", "outcome": "failed", "reason": "resume-exit:126"}
        ],
        "timestamp": "2026-08-03T00:00:00Z",
        "version": 2,
    }
    assert load_failures(path) == failures


def test_main_alerts_a_new_failure_once_then_reports_backoff(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    request = PendingRequest("skill-deploy:demo", "skill-deploy", "demo", "demo")

    class Directory:
        def skill_approvals(self) -> str:
            return "channel"

    class Identity:
        def directory(self) -> Directory:
            return Directory()

    def fake_tick(_gate_dir, _helper, *, eligible, **_kwargs):
        if eligible(request):
            return EnumerationResult((TickResult(request, "failed", "resume-exit:126"),), True)
        return EnumerationResult((TickResult(request, "backoff", "backoff-active"),), True)

    monkeypatch.setenv("SUPPLY_CHAIN_WATCH_STATE", str(tmp_path / "tick.json"))
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_identity", lambda: Identity())
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_owner_id", lambda: "owner")
    monkeypatch.setattr(supply_chain_watch_cli, "watch_tick", fake_tick)
    monkeypatch.setattr(supply_chain_watch_cli.time, "time", lambda: 100.0)

    first = supply_chain_watch_cli.main()
    first_stderr = capsys.readouterr().err
    second = supply_chain_watch_cli.main()
    second_stderr = capsys.readouterr().err

    assert first == 1
    assert "resume-exit:126" in first_stderr
    assert second == 0
    assert "backoff" in second_stderr, "waiting is visible every tick"
    assert "resume-exit:126" not in second_stderr, "but the alert itself fires only once"


def test_main_runs_due_reminders_through_the_existing_gate_api(
    tmp_path: Path, monkeypatch
) -> None:
    request = PendingRequest("skill-deploy:demo", "skill-deploy", "demo", "demo")
    result = TickResult(request, "retain", "unanswered")
    api_calls: list[tuple[str, str, dict[str, str] | None]] = []
    reminder_calls: list[tuple[tuple[TickResult, ...], Path]] = []

    class Directory:
        def skill_approvals(self) -> str:
            return "1528936606856122421"

    class Identity:
        @property
        def api(self):
            return fake_api

        def directory(self) -> Directory:
            return Directory()

    def fake_api(
        method: str, path: str, payload: dict[str, str] | None = None
    ) -> dict[str, str]:
        api_calls.append((method, path, payload))
        if method == "GET":
            return {"guild_id": "1528936606264856737"}
        return {"id": "sent"}

    class Binding:
        channel_id = "bound-channel"

    class Surface:
        def stored(self, record: dict[str, str]) -> Binding:
            assert record["channel_id"] == "untrusted-channel"
            return Binding()

    def fake_remind(
        results: tuple[TickResult, ...], gate_dir: Path, **kwargs
    ) -> tuple[()]:
        reminder_calls.append((results, gate_dir))
        assert kwargs["decision_of"]("message") == "absent"
        assert kwargs["channel_of"]({"channel_id": "untrusted-channel"}) == "bound-channel"
        assert kwargs["guild_of"]("channel") == "1528936606264856737"
        kwargs["deliver"]("channel", "body")
        return ()

    monkeypatch.setenv("SUPPLY_CHAIN_WATCH_STATE", str(tmp_path / "tick.json"))
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_identity", lambda: Identity())
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_owner_id", lambda: "owner")
    monkeypatch.setattr(
        supply_chain_watch_cli.skill_gate,
        "_owner_decision",
        lambda _args, _owner_id, _channel_id: "absent",
    )
    monkeypatch.setattr(
        supply_chain_watch_cli,
        "watch_tick",
        lambda *_args, **_kwargs: EnumerationResult((result,), True),
    )
    monkeypatch.setattr(
        supply_chain_watch_cli.skill_gate_surface,
        "surface_for",
        lambda _kind, _identity: Surface(),
    )
    monkeypatch.setattr(supply_chain_watch_cli, "remind_unanswered", fake_remind)

    assert supply_chain_watch_cli.main() == 0
    assert reminder_calls == [((result,), supply_chain_watch_cli.skill_gate.GATE_DIR)]
    assert api_calls == [
        ("GET", "/channels/channel", None),
        ("POST", "/channels/channel/messages", {"content": "body"}),
    ]


def test_main_retries_as_soon_as_the_release_changes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A suppressed request must not wait out the hour once the release it failed under is gone.

    2026-08-04: three approvals returned resume-exit:5 under one release, the archive defect
    behind that code was fixed in the next, and the following eight ticks did not mention them
    at all. The unit's WorkingDirectory is the sealed release, so ``.origin-sha`` is exactly the
    signal that the cause may be gone.
    """
    request = PendingRequest("skill-deploy:demo", "skill-deploy", "demo", "demo")

    class Directory:
        def skill_approvals(self) -> str:
            return "channel"

    class Identity:
        def directory(self) -> Directory:
            return Directory()

    def fake_tick(_gate_dir, _helper, *, eligible, **_kwargs):
        if eligible(request):
            return EnumerationResult((TickResult(request, "failed", "resume-exit:126"),), True)
        return EnumerationResult((TickResult(request, "backoff", "backoff-active"),), True)

    clock = [100.0]
    release = tmp_path / ".origin-sha"
    _ = release.write_text("release-a\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUPPLY_CHAIN_WATCH_STATE", str(tmp_path / "tick.json"))
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_identity", lambda: Identity())
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_owner_id", lambda: "owner")
    monkeypatch.setattr(supply_chain_watch_cli, "watch_tick", fake_tick)
    monkeypatch.setattr(supply_chain_watch_cli.time, "time", lambda: clock[0])

    failed = supply_chain_watch_cli.main()
    _ = capsys.readouterr()

    clock[0] = 200.0
    suppressed = supply_chain_watch_cli.main()
    suppressed_stderr = capsys.readouterr().err

    _ = release.write_text("release-b\n", encoding="utf-8")
    after_release_change = supply_chain_watch_cli.main()
    changed_stderr = capsys.readouterr().err

    assert failed == 1
    assert suppressed == 0
    assert "backoff" in suppressed_stderr
    assert "resume-exit:126" not in suppressed_stderr, "suppressed, not re-alerted"
    assert after_release_change == 1
    assert "resume-exit:126" in changed_stderr
    assert "backoff" not in changed_stderr


def test_a_suppressed_request_is_reported_waiting_not_silent(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A request serving its backoff must say so every tick — silence reads as gone.

    2026-08-04: after three approvals failed, eight consecutive ticks (11:07 → 11:29)
    mentioned them zero times. In ``journalctl`` that is indistinguishable from the
    requests having disappeared, so the owner had no way to learn their ✅ produced
    nothing. The suppression record already carries the attempt count and the next
    attempt time; the tick simply never printed them.
    """
    request = PendingRequest("skill-deploy:demo", "skill-deploy", "demo", "demo")

    class Directory:
        def skill_approvals(self) -> str:
            return "channel"

    class Identity:
        def directory(self) -> Directory:
            return Directory()

    def fake_tick(_gate_dir, _helper, *, eligible, **_kwargs):
        if eligible(request):
            return EnumerationResult((TickResult(request, "failed", "resume-exit:126"),), True)
        return EnumerationResult((TickResult(request, "backoff", "backoff-active"),), True)

    clock = [100.0]
    _ = (tmp_path / ".origin-sha").write_text("release-a\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUPPLY_CHAIN_WATCH_STATE", str(tmp_path / "tick.json"))
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_identity", lambda: Identity())
    monkeypatch.setattr(supply_chain_watch_cli.skill_gate, "_owner_id", lambda: "owner")
    monkeypatch.setattr(supply_chain_watch_cli, "watch_tick", fake_tick)
    monkeypatch.setattr(supply_chain_watch_cli.time, "time", lambda: clock[0])

    failed = supply_chain_watch_cli.main()
    _ = capsys.readouterr()

    clock[0] = 200.0
    suppressed = supply_chain_watch_cli.main()
    suppressed_stderr = capsys.readouterr().err

    assert failed == 1
    assert suppressed == 0, "waiting is not a unit failure"
    assert "skill-deploy:demo" in suppressed_stderr
    assert "backoff" in suppressed_stderr
    assert "attempt 1" in suppressed_stderr
    assert "retry in" in suppressed_stderr
