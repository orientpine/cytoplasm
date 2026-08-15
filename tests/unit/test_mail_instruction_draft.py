"""W4-6 owner-instruction draft subcommand + watch de-drafting.

Pins: `draft --uid --instruction [--no-post]` creates an instruction-aware
reply draft (classification annotates, never gates), refuses duplicates /
missing reply address (exit 2) and no-go mode (exit 3); `watch` no longer
auto-processes new mail (approval/send loop only); the default reply prompt
is reply-draft-v2.md (with the {{INSTRUCTION}} placeholder).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
import triage_store  # noqa: E402

RULES_PATH = _REPO / "skills" / "mail" / "configs" / "sensitivity-rules.yaml"
INSTRUCTION = "CANARY-지시: 회의는 다음 달로 미루자고 정중히 답해줘"
MAIL_DETAIL = {
    "uid": "u-1",
    "subject": "일정 문의",
    "sender": "가상 발신자 <peer@example.invalid>",
    "body": "다음 주 회의 참석 가능 여부 회신 부탁드립니다.",
}


def _write_stub(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _setup_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: str = "full-go",
    category: str = "important", detail: dict | None = None,
) -> Path:
    """Wire the full draft-pipeline test env; returns the hermes prompt capture path."""
    mode_file = tmp_path / "runtime" / "mail-mode.json"
    if mode:
        mode_file.parent.mkdir(parents=True, exist_ok=True)
        mode_file.write_text(json.dumps({"mode": mode, "source": "test"}), encoding="utf-8")
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_FILE", str(mode_file))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_REPO", str(tmp_path / "absent-repo-mode.json"))
    monkeypatch.setenv("TRIAGE_RULES_FILE", str(RULES_PATH))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(tmp_path / "llm-calls.jsonl"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.delenv("TRIAGE_REPLY_PROMPT", raising=False)
    monkeypatch.delenv("TRIAGE_CLASSIFY_PROMPT", raising=False)
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    glm = _write_stub(
        tmp_path / "glm-stub",
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdin.read()\n"
        f"print('{{\"category\": \"{category}\", \"reply_needed\": true, "
        "\"schedule_needed\": false, \"budget\": false, "
        "\"schedule_text\": \"\", \"reason\": \"test\"}')\n",
    )
    monkeypatch.setenv("TRIAGE_GLM_BIN", str(glm))
    capture = tmp_path / "received-prompt.txt"
    hermes = _write_stub(
        tmp_path / "hermes-stub",
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "pathlib.Path(" + repr(str(capture)) + ").write_text(sys.argv[2], encoding='utf-8')\n"
        "print('{\"subject\": \"Re: x\", \"body\": \"참석 가능합니다. 감사합니다.\"}')\n",
    )
    monkeypatch.setenv("TRIAGE_HERMES_BIN", str(hermes))
    monkeypatch.setattr(triage_cli, "_get_mail", lambda _uid: dict(detail or MAIL_DETAIL))
    return capture


def _run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["triage_cli", *argv])
    return triage_cli.main()


def test_cmd_draft_creates_instruction_draft_no_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a full-go env, a synthetic mail with a reply address, and an owner instruction
    capture = _setup_env(tmp_path, monkeypatch)
    # When: the owner runs draft --no-post
    rc = _run_cli(
        monkeypatch, "draft", "--uid", "u-1", "--instruction", INSTRUCTION, "--no-post"
    )
    # Then: exit 0, DRAFTED marker, frozen-argv draft on disk, instr-draft row, prompt carries the instruction
    assert rc == 0
    assert "DRAFTED draft=" in capsys.readouterr().out
    draft_files = sorted((tmp_path / "gate" / "drafts").glob("*.json"))
    assert len(draft_files) == 1
    record = json.loads(draft_files[0].read_text(encoding="utf-8"))
    assert record["argv"][-2:] == ["--confirm-send", "--json"]
    assert len(record["sha256"]) == 64
    rows = triage_store.processed_rows(tmp_path / "triage.db")
    assert len(rows) == 1 and rows[0][3].startswith("instr-draft:")
    assert INSTRUCTION in capture.read_text(encoding="utf-8")


def test_cmd_draft_refuses_existing_pending_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a first successful instruction draft for the uid
    _setup_env(tmp_path, monkeypatch)
    assert _run_cli(
        monkeypatch, "draft", "--uid", "u-1", "--instruction", INSTRUCTION, "--no-post"
    ) == 0
    capsys.readouterr()
    # When: the identical invocation runs again
    rc = _run_cli(
        monkeypatch, "draft", "--uid", "u-1", "--instruction", INSTRUCTION, "--no-post"
    )
    # Then: refused with exit 2 and a message telling the owner to discard first
    assert rc == 2
    assert "discard" in capsys.readouterr().err


def test_cmd_draft_refuses_no_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a mail detail whose sender yields no reply address
    _setup_env(
        tmp_path, monkeypatch, detail={**MAIL_DETAIL, "sender": "no-address-here"}
    )
    # When: the owner requests an instruction draft
    rc = _run_cli(
        monkeypatch, "draft", "--uid", "u-1", "--instruction", INSTRUCTION, "--no-post"
    )
    # Then: input rejected (exit 2) and no draft exists on disk
    assert rc == 2
    assert list((tmp_path / "gate" / "drafts").glob("*.json")) == []
    capsys.readouterr()


def test_cmd_draft_refuses_no_go_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: no mode file anywhere — effective mode fails closed to no-go
    _setup_env(tmp_path, monkeypatch, mode="")
    # When: the owner requests an instruction draft
    rc = _run_cli(
        monkeypatch, "draft", "--uid", "u-1", "--instruction", INSTRUCTION, "--no-post"
    )
    # Then: refused with the mode exit code 3
    assert rc == 3
    capsys.readouterr()


def test_cmd_draft_normal_category_still_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: classification says "normal" (auto pipeline would skip this mail)
    _setup_env(tmp_path, monkeypatch, category="normal")
    # When: the owner explicitly instructs a draft
    rc = _run_cli(
        monkeypatch, "draft", "--uid", "u-1", "--instruction", INSTRUCTION, "--no-post"
    )
    # Then: the owner instruction is authoritative — draft created, category only annotates
    assert rc == 0
    assert "DRAFTED draft=" in capsys.readouterr().out
    rows = triage_store.processed_rows(tmp_path / "triage.db")
    assert rows[0][1] == "normal" and rows[0][3].startswith("instr-draft:")


def test_watch_no_longer_auto_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a full-go watch tick with no pending drafts and a booby-trapped cmd_process
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "o")
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [])
    monkeypatch.setattr(
        triage_cli, "cmd_process",
        lambda _args: (_ for _ in ()).throw(AssertionError("watch must not auto-process")),
    )
    # When: the production watch tick runs
    rc = triage_cli.cmd_watch(argparse.Namespace())
    # Then: it completes the approval/send loop only — cmd_process is never called
    assert rc == 0


def test_default_reply_prompt_is_v2() -> None:
    # Given: the module-level default reply prompt constant
    prompt = Path(triage_cli.DEFAULT_REPLY_PROMPT)
    # Then: it points at the v2 instruction-aware template, and that file exists
    assert str(prompt).endswith("prompts/reply-draft-v2.md")
    assert prompt.exists()
