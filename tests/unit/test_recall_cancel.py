"""Owner Q433: a recall the caller gave up on stops on the node and on the relay host too.

Real processes, no mocks: the regression was processes that kept running after the SSH reader
was gone, which only a real pipe and a real process group can show.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from automation.knowledge import owner_card
from automation.omo_relay import runner

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "skills/recall/scripts"


def _gone(pgid: int, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _group_script(pidfile: Path) -> tuple[str, ...]:
    return ("sh", "-c", f"echo $$ > {pidfile}; sleep 60 & sleep 60; wait")


def _pid(pidfile: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        text = pidfile.read_text() if pidfile.exists() else ""
        if text.strip():
            return int(text)
        time.sleep(0.05)
    raise AssertionError("child never wrote its pid")


def test_runner_stops_the_whole_omo_group_when_the_caller_hangs_up(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    read_end, write_end = os.pipe()
    os.close(read_end)
    started = time.monotonic()
    with os.fdopen(write_end, "w") as output, pytest.raises(runner.Cancelled):
        runner.complete(_group_script(pidfile), text="prompt", cwd=str(tmp_path), timeout=60, output=output)

    assert time.monotonic() - started < 10
    assert _gone(_pid(pidfile))


def test_runner_timeout_kills_the_group_not_only_the_shell(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    with open(os.devnull, "w") as output, pytest.raises(subprocess.TimeoutExpired):
        runner.complete(_group_script(pidfile), text="prompt", cwd=str(tmp_path), timeout=1, output=output)

    assert _gone(_pid(pidfile))


def test_runner_reports_a_hang_up_as_cancelled_rc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang_up(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise runner.Cancelled

    monkeypatch.setattr(runner, "complete", hang_up)
    import io

    assert runner.run(io.BytesIO(b"prompt"), io.StringIO(), tmp_path) == runner.CANCELLED
    assert '"rc": 130' in (tmp_path / ".dori/omo-relay/calls.jsonl").read_text()


def test_recall_exits_when_its_reader_goes_away() -> None:
    code = "import recall_hangup, time; recall_hangup.exit_on_hangup(); print('READY', flush=True); time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                             env={**os.environ, "PYTHONPATH": str(SCRIPTS)})
    assert child.stdout is not None
    assert child.stdout.readline() == b"READY\n"
    child.stdout.close()

    assert child.wait(timeout=10) == 130


def test_relay_ssh_child_dies_with_its_caller(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess, sys; from automation.omo_relay import client; "
        f"subprocess.Popen(['sh', '-c', 'echo $$ > {pidfile}; exec sleep 60'], preexec_fn=client._die_with_parent); "
        "print('READY', flush=True); import time; time.sleep(60)"
    )
    caller = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, cwd=REPO,
                              env={**os.environ, "PYTHONPATH": str(REPO)})
    assert caller.stdout is not None
    assert caller.stdout.readline() == b"READY\n"
    grandchild = _pid(pidfile)

    caller.send_signal(signal.SIGKILL)
    caller.wait(timeout=5)
    caller.stdout.close()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        status = Path(f"/proc/{grandchild}/stat").read_text().rsplit(")", 1)[1].split()[0]
        if status == "Z":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("relay ssh child outlived its caller")


def test_owner_card_is_the_card_section_only(tmp_path: Path) -> None:
    note = tmp_path / owner_card.MIRROR / owner_card.RELPATH
    note.parent.mkdir(parents=True)
    note.write_text("# 사본\n\n> 머리\n\n## system/ 카드 줄\n\n### human.md\n\n- ATTRIBUTE: 소속\n\n"
                    "## notes/facts/\n\n### 2026-10.md\n\n- 다른 사실\n", encoding="utf-8")

    [(key, [row])] = owner_card.load(tmp_path)

    assert key == owner_card.KEY
    assert row["content"] == "### human.md\n\n- ATTRIBUTE: 소속"
    assert row["metadata"]["path"] == owner_card.RELPATH


@pytest.mark.parametrize("text", [None, "# 사본\n\n## notes/facts/\n\n- 사실\n", "## system/ 카드 줄\n\n## notes/facts/\n"])
def test_no_note_or_no_card_section_gives_no_card(tmp_path: Path, text: str | None) -> None:
    if text is not None:
        note = tmp_path / owner_card.MIRROR / owner_card.RELPATH
        note.parent.mkdir(parents=True)
        note.write_text(text, encoding="utf-8")

    assert owner_card.load(tmp_path) == []


def test_owner_card_path_is_the_gate_memory_copy_path() -> None:
    gate = (REPO / "automation/yoodori_gate/yoodori_read_gate.py").read_text(encoding="utf-8")

    assert f'MEMORY_COPY_RELPATH = "{owner_card.RELPATH}"' in gate
    assert f'MIRROR = HOME / "{owner_card.MIRROR}"' in gate
