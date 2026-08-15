from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_core  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
import triage_store  # noqa: E402


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setenv("TRIAGE_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")


def _draft(tmp_path: Path, *attachments: Path) -> dict:
    return triage_gate.create_draft(
        uid="compose:test", sender="", mail_subject="", to="recipient@example.test",
        subject="offline subject", body="offline body", sensitive=False, tags=(),
        category="compose", flags=(), kind="compose", channel_id="dm-1",
        attachment_paths=tuple(attachments),
    )


def _approval() -> triage_gate.Approval:
    return triage_gate.Approval(ref="reaction:m-1", method="manual_reaction", owner="owner-1")


def test_no_attachment_argv_remains_legacy_shape() -> None:
    assert triage_core.build_send_argv("py", "to", "subject", "body") == (
        "py", "-m", "mailon.main", "send", "--to", "to", "--subject", "subject",
        "--body", "body", "--confirm-send", "--json",
    )


def test_multi_attachment_manifest_is_hash_bound_and_rendered_safely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    first_dir, second_dir = tmp_path / "a", tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "same.txt"
    second = second_dir / "same.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    draft = _draft(tmp_path, first, second)

    assert [item["display_name"] for item in draft["attachments"]] == ["same.txt", "same.txt"]
    assert draft["argv"].count("--attachment") == 2
    assert draft["sha256"] == triage_core.draft_sha256(draft)
    message = triage_core.render_approvals_message(draft)
    assert "첨부: 2개" in message and "text/plain" in message
    assert str(first_dir) not in message
    assert all(item["sha256"] not in message for item in draft["attachments"])


def test_sensitive_reply_hides_attachment_filename_from_approvals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    attachment = tmp_path / "confidential-project-name.pdf"
    attachment.write_bytes(b"fixture")
    draft = _draft(tmp_path, attachment)
    draft.update({"kind": "reply", "sensitive": True})

    message = triage_core.render_approvals_message(draft)

    assert "첨부: 1개" in message
    assert attachment.name not in message and str(tmp_path) not in message


def test_execute_attachment_requires_matching_provider_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    attachment = tmp_path / "plan.pdf"
    attachment.write_bytes(b"fixture")
    draft = _draft(tmp_path, attachment)
    payload = {
        "status": "submitted", "verified": True, "attachment_count": 1,
        "attachment_manifest_sha256": draft["attachment_manifest_sha256"],
    }
    monkeypatch.setattr(
        triage_gate, "_run_send", lambda _argv: (0, json.dumps(payload), "")
    )

    triage_gate.execute_draft(draft, _approval())

    stored = json.loads((tmp_path / "gate" / "drafts" / f"{draft['id']}.json").read_text())
    assert stored["status"] == "executed"
    assert triage_store.consecutive_send_failures(tmp_path / "triage.db") == 0


def test_approved_file_mutation_blocks_before_provider_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    attachment = tmp_path / "plan.txt"
    attachment.write_text("approved", encoding="utf-8")
    draft = _draft(tmp_path, attachment)
    attachment.write_text("changed", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        triage_gate, "_run_send", lambda argv: (calls.append(argv) or (0, "{}", ""))
    )

    with pytest.raises(triage_gate.GateError, match="attachment_invalid"):
        triage_gate.execute_draft(draft, _approval())

    assert calls == []
    stored = json.loads((tmp_path / "gate" / "drafts" / f"{draft['id']}.json").read_text())
    assert stored["status"] == "blocked"


def test_upload_failure_preserves_stable_code_without_send_counter_or_path_leak(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    attachment = tmp_path / "private-name.txt"
    attachment.write_text("secret bytes", encoding="utf-8")
    draft = _draft(tmp_path, attachment)
    payload = {
        "status": "error", "error_code": "attachment_upload_failed",
        "stage": "upload", "retryable": True,
    }
    monkeypatch.setattr(
        triage_gate, "_run_send",
        lambda _argv: (2, json.dumps(payload), f"provider failed at {attachment}"),
    )

    with pytest.raises(triage_gate.GateError) as caught:
        triage_gate.execute_draft(draft, _approval())

    message = str(caught.value)
    assert "error_code=attachment_upload_failed" in message
    assert str(attachment) not in message and "private-name.txt" not in message
    assert triage_store.consecutive_send_failures(tmp_path / "triage.db") == 0
    stored = json.loads((tmp_path / "gate" / "drafts" / f"{draft['id']}.json").read_text())
    assert stored["status"] == "pending"


def test_policy_rejects_unsupported_file_before_draft(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    executable = tmp_path / "unsafe.exe"
    executable.write_bytes(b"not-real")
    with pytest.raises(triage_core.AttachmentPolicyError) as caught:
        _draft(tmp_path, executable)
    assert caught.value.error_code == "attachment_unsupported"
    assert list((tmp_path / "gate" / "drafts").glob("*.json")) == []
