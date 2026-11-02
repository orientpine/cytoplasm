"""YooDori gate: `mail-compose --reply-gmail <hex id>` (owner 2026-11-02 "지메일로 써").

New file: test_yoodori_gate.py pins the original card argv byte for byte. Before this the
gate refused the flag as unknown and required --to, so a Gmail reply had no card path.
"""
from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate

REPO = Path(__file__).resolve().parents[2]
ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]
MESSAGE = "1a19c50944a8fa9b"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def test_reply_gmail_reaches_compose_without_to() -> None:
    name, argv, _cwd = gate.build(["mail-compose", "--reply-gmail", MESSAGE,
                                   "--body-b64", _b64("확인했습니다\n감사합니다"), "--cc", "c@example.invalid", *ORIGIN])
    assert name == "mail-compose" and argv[2] == "compose"
    assert argv[argv.index("--reply-gmail") + 1] == MESSAGE
    assert argv[argv.index("--body") + 1] == "확인했습니다\n감사합니다" and "--to" not in argv
    script = ("import sys; sys.path.insert(0, sys.argv[1]); import triage_cli; "
              "a = triage_cli.build_parser().parse_args(sys.argv[2:]); assert a.reply_gmail and not a.to")
    parsed = subprocess.run([sys.executable, "-c", script, str(REPO / "skills/mail/scripts"), *argv[2:]],
                            cwd=REPO, capture_output=True, text=True, check=False)
    assert parsed.returncode == 0, parsed.stderr


@pytest.mark.parametrize("args", [
    ["--reply-gmail", "not-hex!", "--body", "b"],
    ["--reply-gmail", MESSAGE],
    ["--reply-gmail", MESSAGE, "--body", "b", "--to", "a@example.invalid"],
    ["--reply-gmail", MESSAGE, "--body", "b", "--subject", "s"],
    ["--reply-gmail", MESSAGE, "--body", "b", "--bcc", "a@example.invalid"],
    ["--reply-gmail", MESSAGE, "--body", "b", "--forward-gmail", MESSAGE],
    ["--reply-gmail", MESSAGE, "--body", "b", "--in-reply-to", "12"],
    ["--reply-gmail", MESSAGE, "--reply-gmail", MESSAGE, "--body", "b"],
    ["--body", "b", "--subject", "s"],
])
def test_reply_gmail_misuse_is_refused(args: list[str]) -> None:
    with pytest.raises(gate.Refused):
        gate.build(["mail-compose", *args, *ORIGIN])


def test_reply_gmail_needs_the_owner_message() -> None:
    with pytest.raises(gate.Refused):
        gate.build(["mail-compose", "--reply-gmail", MESSAGE, "--body", "b"])
