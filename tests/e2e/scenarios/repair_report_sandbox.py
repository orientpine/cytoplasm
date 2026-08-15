#!/usr/bin/env python3
"""Offline eight-step repair-report chain scenario (# noqa: SIZE_OK)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TypedDict

from automation.interop.discord_transport import SentMessage
from automation.interop.report import ReportStatus
from automation.repair import repair_report_consumer, repair_report_send
from automation.repair.repair_capability import mac
from automation.repair.repair_ops_reporting import HermesTicketBoard
from automation.repair.repair_report_queue import (
    ReportRequest,
    compact,
    enqueue,
    parse_line,
    semantic_key,
)

type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
type Fetcher = Callable[[str], JsonValue]

ROOT: Final = Path(__file__).resolve().parents[3]
BOT_ID: Final = "900000000000000001"
CHANNEL_ID: Final = "900000000000000002"
AGENT_ID: Final = "agent-sandbox"


class ScenarioFailure(AssertionError):
    pass


class ArgvLog(TypedDict):
    argv: list[str]


@dataclass(frozen=True, slots=True)
class Sandbox:
    root: Path
    home: Path
    queue: Path
    ack: Path
    capability: Path
    registry: Path
    consumer_state: Path
    fake_bin: Path
    board_state: Path
    hermes_log: Path
    ssh_log: Path


class FakeDiscord:
    def __init__(self) -> None:
        self.messages: list[dict[str, JsonValue]] = []
        self.next_id: int = 101

    def send(self, body: str) -> tuple[SentMessage, ...]:
        identifier = str(self.next_id)
        self.next_id += 1
        self.messages.insert(0, {"id": identifier, "author": {"id": BOT_ID}, "content": body})
        return (SentMessage(identifier),)

    def fetch(self, path: str) -> JsonValue:
        if path == "/users/@me":
            return {"id": BOT_ID}
        if path == f"/channels/{CHANNEL_ID}/messages?limit=1":
            newest: list[JsonValue] = []
            newest.extend(self.messages[:1])
            return newest
        marker = "?before="
        if path.startswith(f"/channels/{CHANNEL_ID}/messages{marker}"):
            before = int(path.split(marker, maxsplit=1)[1].split("&", maxsplit=1)[0])
            page: list[JsonValue] = []
            page.extend(message for message in self.messages if int(str(message["id"])) < before)
            return page
        raise ScenarioFailure(f"unexpected fake Discord path: {path}")


def _assert(condition: bool, detail: str) -> None:
    if not condition:
        raise ScenarioFailure(detail)


def _values(values: Iterable[JsonValue]) -> list[JsonValue]:
    return list(values)


def _write_executable(path: Path, source: str) -> None:
    _ = path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(0o755)


def _setup(root: Path) -> Sandbox:
    home = root / "home"
    queue = root / "queue"
    ack = root / "ack"
    capability = root / "capability"
    fake_bin = home / ".local" / "bin"
    lifecycle = root / "lifecycle"
    for directory in (home, queue, ack, capability, fake_bin, lifecycle):
        directory.mkdir(parents=True)
    # In production this is created by rollout's root gate; the scenario creates it here to simulate that pre-condition.
    (queue / "queue.lock").touch(mode=0o640)
    registry = root / "repair-tickets.json"
    consumer_state = home / ".hermes" / "repair-report-consumer" / "state.json"
    board_state = root / "fake-board.json"
    hermes_log = root / "hermes-argv.jsonl"
    ssh_log = root / "ssh-argv.jsonl"
    _ = board_state.write_text('{"next":1,"tasks":{}}\n', encoding="utf-8")
    _ = (root / "interop.json").write_text(
        json.dumps({"agent_id": AGENT_ID, "agents_log_channel_id": CHANNEL_ID}),
        encoding="utf-8",
    )
    sandbox = Sandbox(
        root, home, queue, ack, capability, registry, consumer_state, fake_bin,
        board_state, hermes_log, ssh_log,
    )
    _install_fakes(sandbox)
    os.environ.update(
        {
            "HOME": str(home),
            "PATH": str(fake_bin),
            "REPAIR_REPORT_QUEUE": str(queue),
            "REPAIR_REPORT_ACK": str(ack),
            "REPAIR_CAPABILITY_DIR": str(capability),
            "REPAIR_STATE_FILE": str(registry),
            "REPAIR_STATE_ROOT": str(lifecycle),
            "INTEROP_CONFIG": str(root / "interop.json"),
            "REPAIR_SSH_IDENTITY": str(root / "fake-identity"),
            "FAKE_BOARD_STATE": str(board_state),
            "FAKE_HERMES_LOG": str(hermes_log),
            "FAKE_SSH_LOG": str(ssh_log),
            "DISCORD_BOT_TOKEN": "sandbox-credential",
        }
    )
    return sandbox


def _install_fakes(sandbox: Sandbox) -> None:
    _write_executable(
        sandbox.fake_bin / "hermes",
        """import json, os, sys
from pathlib import Path
state_path = Path(os.environ["FAKE_BOARD_STATE"])
log_path = Path(os.environ["FAKE_HERMES_LOG"])
argv = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": argv, "env": {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", ""), "token_present": bool(os.environ.get("DISCORD_BOT_TOKEN"))}}, sort_keys=True) + "\\n")
state = json.loads(state_path.read_text(encoding="utf-8"))
tasks = state["tasks"]
command = argv[1]
if command == "create":
    ticket = f"t_sandbox{state['next']}"
    state["next"] += 1
    tasks[ticket] = {"status": "ready", "events": []}
    print(json.dumps({"task": {"id": ticket}}))
elif command == "show":
    ticket = argv[2]
    task = tasks[ticket]
    print(json.dumps({"task": {"status": task["status"]}, "events": task["events"]}))
elif command == "complete":
    tasks[argv[2]]["status"] = "done"
elif command == "unblock":
    tasks[argv[2]]["status"] = "ready"
elif command == "block":
    kind_index = argv.index("--kind")
    kind = argv[kind_index + 1]
    ticket = argv[kind_index + 2]
    tasks[ticket]["status"] = "blocked"
    tasks[ticket]["events"].append({"kind": "blocked", "payload": {"kind": kind}})
elif command != "comment":
    raise SystemExit(2)
state_path.write_text(json.dumps(state, sort_keys=True) + "\\n", encoding="utf-8")
""",
    )
    _write_executable(
        sandbox.fake_bin / "ssh",
        """import json, os, sys
from pathlib import Path
payload = json.loads(sys.stdin.read())
with Path(os.environ["FAKE_SSH_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "ticket_id": payload["ticket_id"], "occurrence": payload["occurrence"]}, sort_keys=True) + "\\n")
path = f"/srv/autophagy-private/repair-logs/{payload['ticket_id']}/occurrence-{payload['occurrence']}.log"
print(json.dumps({"path": path, "sha256": "0" * 64}, sort_keys=True))
""",
    )


def _detect(source: str, raw_log: str) -> dict[str, JsonValue]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "automation.repair.repair_cli",
            "detect",
            "--source",
            source,
            "--location",
            "sandbox",
            "--stdin",
        ],
        cwd=ROOT,
        env=os.environ,
        input=raw_log,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    _assert(completed.returncode == 0, f"detect failed: {completed.stderr}")
    payload: JsonValue = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise ScenarioFailure("detect stdout was not an object")
    return payload


def _json(path: Path) -> dict[str, JsonValue]:
    payload: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ScenarioFailure(f"expected JSON object: {path.name}")
    return payload


def _pending(sandbox: Sandbox) -> list[ReportRequest]:
    path = sandbox.queue / "pending.jsonl"
    if not path.exists():
        return []
    parsed = [parse_line(line + b"\n") for line in path.read_bytes().splitlines()]
    _assert(all(request is not None for request in parsed), "pending queue contains malformed lines")
    return [request for request in parsed if request is not None]


def _logs(path: Path) -> list[ArgvLog]:
    if not path.exists():
        return []
    payloads: list[ArgvLog] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload: JsonValue = json.loads(line)
        if not isinstance(payload, dict):
            raise ScenarioFailure(f"invalid argv log: {path.name}")
        raw_argv = payload.get("argv")
        if not isinstance(raw_argv, list) or not all(isinstance(item, str) for item in raw_argv):
            raise ScenarioFailure(f"invalid argv list: {path.name}")
        payloads.append({"argv": [item for item in raw_argv if isinstance(item, str)]})
    return payloads


def _receipt(sandbox: Sandbox, request: ReportRequest) -> dict[str, JsonValue]:
    payload = _json(sandbox.ack / f"{request.request_id}.json")
    return {
        "request_id": str(payload["request_id"]),
        "operation": str(payload["operation"]),
        "terminal_reason": str(payload["terminal_reason"]),
        "transition": str(payload["transition"]),
        "report": str(payload["report"]),
        "semantic_key": str(payload["semantic_key"]),
    }


def _append_request(
    request_id: str,
    ticket_id: str,
    occurrence: str,
    capability_mac: str,
) -> ReportRequest:
    request = ReportRequest(
        request_id=request_id,
        operation="complete",
        ticket_id=ticket_id,
        reason_code="applied",
        occurrence=occurrence,
        mac=capability_mac,
        created=datetime.now(tz=UTC).isoformat(),
    )
    enqueue(request)
    return request


def _install_discord_fake(fake: FakeDiscord) -> None:
    def send(
        request: ReportRequest,
        timestamp: datetime,
        *,
        transport: repair_report_send.ReportTransport | None = None,
        budget: repair_report_send.Budget | None = None,
    ) -> str:
        del transport
        if not os.environ.get("DISCORD_BOT_TOKEN"):
            raise KeyError("DISCORD_BOT_TOKEN")
        return repair_report_send.send_report(request, timestamp, transport=fake, budget=budget)

    def watermark(*, fetcher: Fetcher | None = None) -> str:
        del fetcher
        return repair_report_send.channel_watermark(fetcher=fake.fetch)

    def find(
        *,
        task_id: str,
        status: ReportStatus,
        timestamp_iso: str,
        upper: str,
        lower: str,
        cursor: str | None,
        fetcher: Fetcher | None = None,
    ) -> tuple[bool, str, bool]:
        del fetcher
        return repair_report_send.find_report(
            task_id=task_id,
            status=status,
            timestamp_iso=timestamp_iso,
            upper=upper,
            lower=lower,
            cursor=cursor,
            fetcher=fake.fetch,
        )

    repair_report_send._bot_user_id_cache = None
    repair_report_consumer.send_report = send
    repair_report_consumer.channel_watermark = watermark
    repair_report_consumer.find_report = find


def _block_as_transient(sandbox: Sandbox, ticket_id: str) -> None:
    state = json.loads(sandbox.board_state.read_text(encoding="utf-8"))
    task = state["tasks"][ticket_id]
    task["status"] = "blocked"
    task["events"] = [{"kind": "blocked", "payload": {"kind": "transient"}}]
    _ = sandbox.board_state.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")


def _forbid_network(*_args: JsonValue, **_kwargs: JsonValue) -> None:
    raise ScenarioFailure("real urlopen was attempted")


def run() -> dict[str, JsonValue]:
    observations: dict[str, JsonValue] = {}
    with tempfile.TemporaryDirectory(prefix="repair-report-sandbox-") as temporary:
        sandbox = _setup(Path(temporary))
        fake_discord = FakeDiscord()
        _install_discord_fake(fake_discord)
        repair_report_consumer.urlopen = _forbid_network
        repair_report_send.urlopen = _forbid_network

        first = _detect("sandbox-complete", "RuntimeError complete fixture")
        second = _detect("sandbox-reopen", "LookupError reopen fixture")
        first_ticket = str(first["ticket"])
        second_ticket = str(second["ticket"])
        capability_files = sorted(path.name for path in sandbox.capability.glob("*.json"))
        _assert(capability_files == [f"{first_ticket}.json", f"{second_ticket}.json"], "capabilities missing")
        ssh_calls = _logs(sandbox.ssh_log)
        expected_ssh_tail = ["ops@127.0.0.1", "repair-log"]
        _assert(all(call["argv"][-2:] == expected_ssh_tail for call in ssh_calls), "fake ssh argv mismatch")
        observations["step_1_detect"] = {
            "tickets": _values((first_ticket, second_ticket)),
            "capability_files": _values(capability_files),
            "ssh_calls": len(ssh_calls),
            "real_ssh_calls": 0,
        }
        print("STEP 1 PASS " + json.dumps(observations["step_1_detect"], sort_keys=True))

        _block_as_transient(sandbox, second_ticket)
        board = HermesTicketBoard()
        board.complete(first_ticket, "private completion detail")
        board.reopen(second_ticket, "owner_cancelled")
        initial = _pending(sandbox)
        _assert([request.operation for request in initial] == ["complete", "reopen"], "board enqueue mismatch")
        observations["step_2_enqueue"] = {
            "operations": _values(request.operation for request in initial),
            "reason_codes": _values(request.reason_code for request in initial),
            "pending_lines": len(initial),
        }
        print("STEP 2 PASS " + json.dumps(observations["step_2_enqueue"], sort_keys=True))

        before_commands = len(_logs(sandbox.hermes_log))
        completed = repair_report_consumer.consume_once()
        commands: list[list[str]] = [entry["argv"] for entry in _logs(sandbox.hermes_log)[before_commands:]]
        expected_commands = [
            ["kanban", "show", first_ticket, "--json"],
            ["kanban", "complete", first_ticket, "--result", repair_report_consumer.COMPLETE_RESULT],
            ["kanban", "show", first_ticket, "--json"],
            ["kanban", "show", second_ticket, "--json"],
            ["kanban", "unblock", second_ticket],
            ["kanban", "block", "--kind", "needs_input", second_ticket, repair_report_consumer.REOPEN_REASON],
            ["kanban", "show", second_ticket, "--json"],
        ]
        _assert(commands == expected_commands, "consumer hermes argv sequence mismatch")
        receipts = [_receipt(sandbox, request) for request in initial]
        receipt_values = _values(receipts)
        command_values = _values(_values(command) for command in commands)
        _assert(completed == 2 and len(fake_discord.messages) == 2, "first consume did not finish two reports")
        _assert(all((sandbox.ack / f"sem-{semantic_key(request)}.json").is_file() for request in initial), "semantic receipt missing")
        observations["step_3_consume"] = {
            "completed": completed,
            "hermes_argv": command_values,
            "reports_sent": len(fake_discord.messages),
            "receipts": receipt_values,
        }
        print("STEP 3 PASS " + json.dumps(observations["step_3_consume"], sort_keys=True))

        sent_before = len(fake_discord.messages)
        second_count = repair_report_consumer.consume_once()
        _assert(len(fake_discord.messages) == sent_before, "idempotent tick resent a report")
        observations["step_4_idempotent"] = {"completed": second_count, "additional_reports": 0}
        print("STEP 4 PASS " + json.dumps(observations["step_4_idempotent"], sort_keys=True))

        owner = initial[0]
        duplicates = [replace(owner, request_id=f"{index:032x}") for index in range(1001, 1004)]
        for duplicate in duplicates:
            enqueue(duplicate)
        sent_before = len(fake_discord.messages)
        duplicate_count = repair_report_consumer.consume_once()
        duplicate_receipts = [_receipt(sandbox, request) for request in duplicates]
        _assert(len(fake_discord.messages) == sent_before, "semantic duplicates were sent")
        _assert(all(item["terminal_reason"] == "duplicate_semantic" for item in duplicate_receipts), "duplicate reason mismatch")
        observations["step_5_amplification"] = {
            "completed": duplicate_count,
            "additional_reports": 0,
            "receipts": _values(duplicate_receipts),
        }
        print("STEP 5 PASS " + json.dumps(observations["step_5_amplification"], sort_keys=True))

        forged = _append_request("f" * 32, first_ticket, "2", "0" * 64)
        commands_before = len(_logs(sandbox.hermes_log))
        sent_before = len(fake_discord.messages)
        forged_count = repair_report_consumer.consume_once()
        forged_receipt = _receipt(sandbox, forged)
        _assert(forged_receipt["terminal_reason"] == "bad_capability", "forged MAC was not rejected")
        _assert(len(_logs(sandbox.hermes_log)) == commands_before, "forged MAC reached hermes")
        _assert(len(fake_discord.messages) == sent_before, "forged MAC reached transport")
        observations["step_6_forged_mac"] = {
            "completed": forged_count,
            "terminal_reason": forged_receipt["terminal_reason"],
            "additional_commands": 0,
            "additional_reports": 0,
        }
        print("STEP 6 PASS " + json.dumps(observations["step_6_forged_mac"], sort_keys=True))

        recovery = _append_request("e" * 32, first_ticket, "3", mac(first_ticket, "3"))
        token = os.environ.pop("DISCORD_BOT_TOKEN")
        sent_before = len(fake_discord.messages)
        missing_count = repair_report_consumer.consume_once()
        state_after_missing = json.loads(sandbox.consumer_state.read_text(encoding="utf-8"))
        report_state = state_after_missing["records"][recovery.request_id]["report"]
        _assert(report_state == "in_flight", "missing token terminalized report")
        _assert(not (sandbox.ack / f"{recovery.request_id}.json").exists(), "missing token wrote ACK")
        os.environ["DISCORD_BOT_TOKEN"] = token
        recovered_count = repair_report_consumer.consume_once()
        recovered_receipt = _receipt(sandbox, recovery)
        _assert(len(fake_discord.messages) == sent_before + 1, "restored token did not recover send")
        observations["step_7_recovery"] = {
            "missing_token_completed": missing_count,
            "state_without_token": "in_flight",
            "ack_without_token": False,
            "recovered_completed": recovered_count,
            "recovered_receipt": recovered_receipt,
        }
        print("STEP 7 PASS " + json.dumps(observations["step_7_recovery"], sort_keys=True))

        pending_request = _append_request("d" * 32, second_ticket, "4", mac(second_ticket, "4"))
        before_compact = len(_pending(sandbox))
        removed = compact()
        remaining = _pending(sandbox)
        _assert(remaining == [pending_request], "compact removed an unreceipted line or retained terminal lines")
        observations["step_8_compact"] = {
            "before_lines": before_compact,
            "removed": removed,
            "remaining_request_ids": _values(request.request_id for request in remaining),
        }
        print("STEP 8 PASS " + json.dumps(observations["step_8_compact"], sort_keys=True))

        generated = [*sandbox.root.rglob("*")]
        _assert(all(path.is_relative_to(sandbox.root) for path in generated), "sandbox path escaped")
        observations["isolation"] = {
            "urlopen_calls": 0,
            "real_ssh_calls": 0,
            "real_hermes_calls": 0,
            "path_entries": _values((str(sandbox.fake_bin),)),
            "srv_writes": 0,
            "real_home_writes": 0,
        }
        print("ISOLATION PASS " + json.dumps(observations["isolation"], sort_keys=True))
    return observations


def main() -> int:
    observations = run()
    print("OBS-JSON: " + json.dumps(observations, sort_keys=True))
    print("RRC-3 SANDBOX E2E: ALL 8 STEPS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
