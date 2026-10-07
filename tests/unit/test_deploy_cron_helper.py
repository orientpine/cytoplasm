r"""cron 등록은 목록을 읽은 뒤에만 만들거나 제자리에서 고친다(`automation/deploy_cron.sh`).

배포기마다 따로 쓰던 등록 줄은 셋이 서로 달랐다: 어떤 것은 `--all` 없이 목록을 읽어 일시정지 잡을
못 보고 같은 잡을 하나 더 만들 수 있었고, 어떤 것은 주기를 아예 고치지 않았으며, 이름이 둘인 잡이나
에이전트 모드 잡을 가리지 않았다. 공용 헬퍼 `converge_cron` 하나가 그 분기를 소유한다.

가짜 `hermes` 는 노드에서 실측한 `hermes cron list --all` 형식(머리 상자 뒤에 `  <16진 id> [active]`
줄과 들여쓴 `Name:`·`Schedule:`… 줄)을 그대로 내고, 상태 파일을 들고 create·edit 를 반영하며 모든
호출을 기록한다. 단언은 출력 문구가 아니라 호출 기록·상태 파일·종료코드로만 한다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_HELPER = _REPO / "automation" / "deploy_cron.sh"

_NAME = "mail-triage-watch"
_SCHEDULE = "*/2 * * * *"
_SCRIPT = "mail_triage_watch.py"
_DELIVER = "discord"

_FAKE_HERMES = r'''#!/usr/bin/env python3
"""노드의 `hermes cron` 을 흉내 내는 가짜 — 상태 파일을 읽고 쓰며 모든 호출을 기록한다."""
import json
import os
import sys

state_path = os.environ["FAKE_HERMES_STATE"]
with open(state_path, encoding="utf-8") as handle:
    state = json.load(handle)
args = sys.argv[1:]
with open(os.environ["FAKE_HERMES_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")


def save():
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)


def option(name):
    return args[args.index(name) + 1]


def hang():
    with open(state["hang_fifo"], encoding="utf-8") as handle:
        handle.read()


if args[:2] == ["cron", "list"]:
    if state.get("list_fails"):
        sys.stderr.write("cron store unreadable\n")
        sys.exit(1)
    if state.get("list_hangs"):
        hang()
    if "raw_listing" in state:
        sys.stdout.write(state["raw_listing"])
        sys.exit(0)
    if not any("--all" in args or not job.get("paused") for job in state["jobs"]):
        sys.stdout.write("No scheduled jobs.\nCreate one with 'hermes cron create ...' or the /cron command in chat.\n")
        sys.exit(0)
    lines = [
        "\u250c" + "\u2500" * 40 + "\u2510",
        "\u2502" + "Scheduled Jobs".center(40) + "\u2502",
        "\u2514" + "\u2500" * 40 + "\u2518",
    ]
    for job in state["jobs"]:
        if job.get("paused") and "--all" not in args:
            continue
        lines.append(f"  {job['id']} [{'paused' if job.get('paused') else 'active'}]")
        lines.append(f"    Name:      {job['name']}")
        lines.append(f"    Schedule:  {job['schedule']}")
        lines.append("    Repeat:    \u221e")
        lines.append("    Next run:  2026-10-01T09:00:00+09:00")
        lines.append(f"    Deliver:   {job['deliver']}")
        if job.get("skills"):
            lines.append(f"    Skills:    {job['skills']}")
        else:
            lines.append(f"    Script:    {job['script']}")
            lines.append("    Mode:      no-agent (script stdout delivered directly)")
        lines.append("    Last run:  2026-09-30T09:00:01+09:00  ok")
        lines.append("")
    text = "\n".join(lines) + "\n"
    if "truncate_at" in state:
        text = text[: state["truncate_at"]]
    sys.stdout.write(text)
    sys.exit(0)

if args[:2] == ["cron", "create"]:
    if "--no-agent" not in args:
        sys.exit(2)
    if state.get("create_hangs") == "before-apply":
        hang()
    state["jobs"].append({
        "id": format(0xA0000 + len(state["jobs"]), "012x"),
        "name": option("--name"),
        "schedule": args[2],
        "script": option("--script"),
        "deliver": option("--deliver"),
    })
    save()
    if state.get("create_hangs") == "after-apply":
        hang()
    sys.exit(0)

if args[:2] == ["cron", "edit"]:
    if state.get("ignore_edit"):
        sys.exit(0)
    for job in state["jobs"]:
        if job["id"] == args[2]:
            job.update(schedule=option("--schedule"), deliver=option("--deliver"),
                       script=option("--script"))
            save()
            sys.exit(0)
    sys.exit(3)

sys.exit(2)
'''

_FLOCK_SHIM = """#!/bin/bash
# 실제 flock 으로 잠금을 겨루되, 명세의 -w 60 을 받았는지 기록하고 기다리지 않고(-n) 판정한다.
printf '%s\\n' "$*" >> "$FAKE_FLOCK_LOG"
[ "$1" = -w ] && [ "$2" = 60 ] || exit 98
shift 2
exec {real} -n "$@"
"""


def _job(job_id: str, name: str = _NAME, **values: object) -> dict[str, object]:
    job: dict[str, object] = {
        "id": job_id,
        "name": name,
        "schedule": _SCHEDULE,
        "script": _SCRIPT,
        "deliver": _DELIVER,
    }
    job.update(values)
    return job


_NEIGHBOURS = (
    _job("5dd6adf15063", "mail-daily-digest", schedule="0 8 * * *", script="mail_digest_watch.py"),
    _job("b87a390e9561", "memory-curator-watch", schedule="every 30m",
         script="memory_curator_watch.py", deliver="local"),
)


class _Node:
    """가짜 노드 홈 하나 — `$HOME/.local/bin` 에 가짜 hermes 를 두고 상태·호출 기록을 들고 있다."""

    def __init__(self, tmp_path: Path, jobs: list[dict[str, object]], **flags: object) -> None:
        self.home = tmp_path / "node-home"
        self.bin = self.home / ".local" / "bin"
        self.bin.mkdir(parents=True)
        self.state = tmp_path / "state.json"
        self.log = tmp_path / "calls.log"
        self.agent_marker = tmp_path / "run_agent.called"
        self.flock_log = tmp_path / "flock.log"
        _ = self.state.write_text(json.dumps({"jobs": jobs, **flags}), encoding="utf-8")
        hermes = self.bin / "hermes"
        _ = hermes.write_text(_FAKE_HERMES, encoding="utf-8")
        hermes.chmod(0o755)

    def converge(self, *args: str, timeout: str | None = None) -> subprocess.CompletedProcess[str]:
        argv = args or (_NAME, _SCHEDULE, _SCRIPT, _DELIVER)
        stub = (
            'run_agent() { : > "$AGENT_MARKER"; HOME="$NODE_HOME" bash -c "$1" < /dev/null; }; '
            f'source "{_HELPER}"; converge_cron "$@"'
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "NODE_HOME": str(self.home),
            "AGENT_MARKER": str(self.agent_marker),
            "FAKE_HERMES_STATE": str(self.state),
            "FAKE_HERMES_LOG": str(self.log),
            "FAKE_FLOCK_LOG": str(self.flock_log),
        }
        if timeout is not None:
            env["DEPLOY_CRON_TIMEOUT"] = timeout
        return subprocess.run(
            ("bash", "-c", stub, "converge", *argv),
            capture_output=True, text=True, check=False, env=env, timeout=60,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def mutations(self) -> list[list[str]]:
        return [call for call in self.calls() if call[1] in ("create", "edit")]

    def jobs(self) -> list[dict[str, object]]:
        return json.loads(self.state.read_text(encoding="utf-8"))["jobs"]


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return f"rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"


def test_absent_job_is_created_once(tmp_path: Path) -> None:
    node = _Node(tmp_path, list(_NEIGHBOURS))

    result = node.converge()

    assert result.returncode == 0, _detail(result)
    assert node.mutations() == [[
        "cron", "create", _SCHEDULE, "--name", _NAME, "--no-agent",
        "--script", _SCRIPT, "--deliver", _DELIVER,
    ]]
    assert [job["name"] for job in node.jobs()].count(_NAME) == 1


def test_matching_job_is_left_alone(tmp_path: Path) -> None:
    node = _Node(tmp_path, [*_NEIGHBOURS, _job("7949b736a3d0")])

    result = node.converge()

    assert result.returncode == 0, _detail(result)
    assert node.mutations() == []


def test_whitespace_only_schedule_difference_is_not_drift(tmp_path: Path) -> None:
    node = _Node(tmp_path, [_job("7949b736a3d0", schedule="*/2  *  * * *")])

    result = node.converge()

    assert result.returncode == 0, _detail(result)
    assert node.mutations() == []


def test_schedule_drift_is_edited_in_place(tmp_path: Path) -> None:
    node = _Node(tmp_path, [*_NEIGHBOURS, _job("7949b736a3d0", schedule="*/10 * * * *")])

    result = node.converge()

    assert result.returncode == 0, _detail(result)
    assert node.mutations() == [[
        "cron", "edit", "7949b736a3d0", "--schedule", _SCHEDULE, "--deliver", _DELIVER,
        "--no-agent", "--script", _SCRIPT,
    ]]
    mine = [job for job in node.jobs() if job["name"] == _NAME]
    assert [(job["id"], job["schedule"]) for job in mine] == [("7949b736a3d0", _SCHEDULE)]


def test_deliver_drift_is_edited_in_place(tmp_path: Path) -> None:
    node = _Node(tmp_path, [_job("7949b736a3d0", deliver="local")])

    result = node.converge()

    assert result.returncode == 0, _detail(result)
    assert [call[1:3] for call in node.mutations()] == [["edit", "7949b736a3d0"]]
    assert node.jobs()[0]["deliver"] == _DELIVER


def test_duplicate_names_block_without_changes(tmp_path: Path) -> None:
    node = _Node(tmp_path, [_job("7949b736a3d0"), _job("0259df429aa5", schedule="*/10 * * * *")])

    result = node.converge()

    assert result.returncode == 7, _detail(result)
    assert node.mutations() == []


def test_failed_listing_never_creates(tmp_path: Path) -> None:
    node = _Node(tmp_path, [], list_fails=True)

    result = node.converge()

    assert result.returncode == 4, _detail(result)
    assert node.mutations() == []


def test_truncated_listing_never_creates(tmp_path: Path) -> None:
    """목록이 잡 한가운데에서 잘리면 그 잡이 없다고 읽어서는 안 된다."""
    node = _Node(tmp_path, [_job("7949b736a3d0")], truncate_at=170)

    result = node.converge()

    assert result.returncode == 4, _detail(result)
    assert node.mutations() == []


def test_paused_job_is_seen_and_not_duplicated(tmp_path: Path) -> None:
    node = _Node(tmp_path, [*_NEIGHBOURS, _job("7949b736a3d0", paused=True)])

    result = node.converge()

    assert result.returncode == 0, _detail(result)
    assert node.mutations() == []
    assert all("--all" in call for call in node.calls() if call[:2] == ["cron", "list"])


def test_paused_drifted_job_is_edited_and_stays_paused(tmp_path: Path) -> None:
    node = _Node(tmp_path, [_job("7949b736a3d0", schedule="*/10 * * * *", paused=True)])

    result = node.converge()

    assert result.returncode == 0, _detail(result)
    assert [call[1:3] for call in node.mutations()] == [["edit", "7949b736a3d0"]]
    assert node.jobs()[0]["paused"] is True


def test_script_registered_under_another_name_blocks_creation(tmp_path: Path) -> None:
    node = _Node(tmp_path, [_job("7949b736a3d0", name="mail-triage-legacy")])

    result = node.converge()

    assert result.returncode == 7, _detail(result)
    assert node.mutations() == []


def test_agent_mode_job_with_the_same_name_is_never_edited(tmp_path: Path) -> None:
    node = _Node(tmp_path, [_job("7949b736a3d0", schedule="0 3 * * *", skills="mail")])

    result = node.converge()

    assert result.returncode == 7, _detail(result)
    assert node.mutations() == []


def test_an_edit_that_does_not_stick_fails_verification(tmp_path: Path) -> None:
    """edit 가 0 을 내도 다시 읽은 목록이 원하는 값이 아니면 성공이 아니다."""
    node = _Node(tmp_path, [_job("7949b736a3d0", schedule="*/10 * * * *")], ignore_edit=True)

    result = node.converge()

    assert result.returncode == 5, _detail(result)
    assert len(node.mutations()) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ("mail;touch pwned", _SCHEDULE, _SCRIPT, _DELIVER),
        (_NAME, '*/2 * * * *"', _SCRIPT, _DELIVER),
        (_NAME, _SCHEDULE, "$(id).py", _DELIVER),
        (_NAME, _SCHEDULE, _SCRIPT, "discord`id`"),
        (_NAME, _SCHEDULE, _SCRIPT, ""),
    ],
)
def test_unsafe_arguments_are_rejected_before_any_remote_call(
    tmp_path: Path, argv: tuple[str, str, str, str]
) -> None:
    node = _Node(tmp_path, [])

    result = node.converge(*argv)

    assert result.returncode == 2, _detail(result)
    assert not node.agent_marker.exists()
    assert node.calls() == []


def test_a_held_converge_lock_fails_without_touching_cron(tmp_path: Path) -> None:
    real_flock = shutil.which("flock")
    assert real_flock is not None
    node = _Node(tmp_path, [])
    shim = node.bin / "flock"
    _ = shim.write_text(_FLOCK_SHIM.format(real=real_flock), encoding="utf-8")
    shim.chmod(0o755)
    lock = node.home / ".hermes" / "cron" / ".autophagy-converge.lock"
    lock.parent.mkdir(parents=True)
    ready = tmp_path / "ready.fifo"
    release = tmp_path / "release.fifo"
    os.mkfifo(ready)
    os.mkfifo(release)
    holder = subprocess.Popen(
        (real_flock, str(lock), "bash", "-c", 'printf ready > "$1"; read -r _ < "$2"',
         "holder", str(ready), str(release)),
    )
    try:
        assert ready.read_text(encoding="utf-8") == "ready"

        result = node.converge()
    finally:
        _ = release.write_text("go\n", encoding="utf-8")
        assert holder.wait(timeout=30) == 0

    assert result.returncode == 6, _detail(result)
    assert node.calls() == []
    assert node.flock_log.read_text(encoding="utf-8").split() == ["-w", "60", "9"]


_BOX = ["\u250c" + "\u2500" * 40 + "\u2510", "\u2502 Scheduled Jobs \u2502", "\u2514" + "\u2500" * 40 + "\u2518"]


def _block(header: str, name: str, schedule: str, script: str) -> list[str]:
    return [
        header,
        f"    Name:      {name}",
        f"    Schedule:  {schedule}",
        "    Repeat:    \u221e",
        "    Next run:  2026-10-01T09:00:00+09:00",
        "    Deliver:   discord",
        f"    Script:    {script}",
        "    Mode:      no-agent (script stdout delivered directly)",
        "    Last run:  2026-09-30T09:00:01+09:00  ok",
        "",
    ]


_DIGEST_BLOCK = _block("  5dd6adf15063 [active]", "mail-daily-digest", "0 8 * * *", "mail_digest_watch.py")


def _raw(*lines: str) -> str:
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    "header",
    [
        "  7949B736A3D0 [active]",
        "  7949b736a3d0 [active] (repeat 3/5)",
        "  7949b736a3d0 [paused by user]",
        "  7949b736a3d0  [active]",
    ],
)
def test_an_unrecognised_header_never_reattributes_fields(tmp_path: Path, header: str) -> None:
    """모르는 머리 줄 뒤의 필드가 앞 잡의 id 에 붙어 엉뚱한 잡을 고치던 결함(F1)의 회귀."""
    listing = _raw(*_BOX, *_DIGEST_BLOCK, *_block(header, _NAME, "*/10 * * * *", _SCRIPT))
    node = _Node(tmp_path, [], raw_listing=listing)

    result = node.converge()

    assert result.returncode == 4, _detail(result)
    assert node.mutations() == []


def test_a_listing_cut_inside_a_header_is_unreadable(tmp_path: Path) -> None:
    node = _Node(tmp_path, [], raw_listing=_raw(*_BOX, *_DIGEST_BLOCK) + "  b8")

    result = node.converge()

    assert result.returncode == 4, _detail(result)
    assert node.mutations() == []


def test_a_repeated_key_inside_one_block_is_unreadable(tmp_path: Path) -> None:
    block = _block("  5dd6adf15063 [active]", "mail-daily-digest", "0 8 * * *", "mail_digest_watch.py")
    block.insert(5, f"    Name:      {_NAME}")
    node = _Node(tmp_path, [], raw_listing=_raw(*_BOX, *block))

    result = node.converge()

    assert result.returncode == 4, _detail(result)
    assert node.mutations() == []


def _hanging_node(tmp_path: Path, jobs: list[dict[str, object]], **flags: object) -> _Node:
    fifo = tmp_path / "never-written.fifo"
    os.mkfifo(fifo)
    return _Node(tmp_path, jobs, hang_fifo=str(fifo), **flags)


def test_a_hung_listing_times_out_without_changes(tmp_path: Path) -> None:
    node = _hanging_node(tmp_path, [], list_hangs=True)

    started = time.monotonic()
    result = node.converge(timeout="1")
    elapsed = time.monotonic() - started

    assert result.returncode == 4, _detail(result)
    assert node.mutations() == []
    assert elapsed < 20


def test_a_create_that_hangs_after_applying_is_judged_by_the_re_list(tmp_path: Path) -> None:
    node = _hanging_node(tmp_path, [], create_hangs="after-apply")

    result = node.converge(timeout="1")

    assert result.returncode == 0, _detail(result)
    assert [job["name"] for job in node.jobs()] == [_NAME]
    assert [call[1] for call in node.calls()] == ["list", "create", "list"]


def test_a_create_that_hangs_before_applying_fails_verification(tmp_path: Path) -> None:
    node = _hanging_node(tmp_path, [], create_hangs="before-apply")

    result = node.converge(timeout="1")

    assert result.returncode == 5, _detail(result)
    assert node.jobs() == []


@pytest.mark.parametrize("value", ["0", "abc", "1;id", ""])
def test_an_invalid_timeout_override_is_rejected_before_any_remote_call(
    tmp_path: Path, value: str
) -> None:
    node = _Node(tmp_path, [])

    result = node.converge(timeout=value)

    assert result.returncode == 2, _detail(result)
    assert not node.agent_marker.exists()
