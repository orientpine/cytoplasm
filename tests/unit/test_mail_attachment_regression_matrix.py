from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

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


def _draft(*attachments: Path) -> dict:
    return triage_gate.create_draft(
        uid="compose:matrix",
        sender="",
        mail_subject="",
        to="recipient@example.test",
        subject="offline subject",
        body="offline body",
        sensitive=False,
        tags=(),
        category="compose",
        flags=(),
        kind="compose",
        channel_id="dm-matrix",
        attachment_paths=tuple(attachments),
    )


def _approval() -> triage_gate.Approval:
    return triage_gate.Approval(
        ref="reaction:matrix", method="manual_reaction", owner="owner-matrix"
    )


def _submitted_payload(draft: dict, attachment_count: int) -> str:
    payload = {"status": "submitted"}
    if attachment_count:
        payload.update(
            {
                "verified": True,
                "attachment_count": attachment_count,
                "attachment_manifest_sha256": draft["attachment_manifest_sha256"],
            }
        )
    return json.dumps(payload)


def _stored_draft(tmp_path: Path, draft: dict) -> dict:
    draft_id = draft["id"]
    path = tmp_path / "gate" / "drafts" / f"{draft_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _attachment_values(argv: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(value for flag, value in zip(argv, argv[1:]) if flag == "--attachment")


def test_approval_send_keeps_no_attachment_flow_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    draft = _draft()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        triage_gate,
        "_run_send",
        lambda argv: calls.append(argv) or (0, _submitted_payload(draft, 0), ""),
    )

    triage_gate.execute_draft(draft, _approval())

    assert len(calls) == 1
    assert "--attachment" not in calls[0]
    assert _stored_draft(tmp_path, draft)["status"] == "executed"


def test_approval_send_uploads_one_attachment_and_keeps_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    attachment = tmp_path / "single.bin"
    attachment.write_bytes(b"single")
    draft = _draft(attachment)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        triage_gate,
        "_run_send",
        lambda argv: calls.append(argv) or (0, _submitted_payload(draft, 1), ""),
    )

    triage_gate.execute_draft(draft, _approval())

    assert _attachment_values(calls[0]) == (str(attachment.resolve()),)
    assert _stored_draft(tmp_path, draft)["status"] == "executed"


def test_approval_send_uploads_multiple_attachments_in_input_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    draft = _draft(first, second)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        triage_gate,
        "_run_send",
        lambda argv: calls.append(argv) or (0, _submitted_payload(draft, 2), ""),
    )

    triage_gate.execute_draft(draft, _approval())

    assert _attachment_values(calls[0]) == (
        str(first.resolve()),
        str(second.resolve()),
    )
    assert _stored_draft(tmp_path, draft)["status"] == "executed"


def test_approval_send_aborts_after_upload_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    attachment = tmp_path / "upload.bin"
    attachment.write_bytes(b"upload")
    draft = _draft(attachment)
    calls: list[tuple[str, ...]] = []
    payload = json.dumps(
        {
            "status": "error",
            "error_code": "attachment_upload_failed",
            "stage": "attachment_upload",
            "retryable": True,
            "message": "provider detail must not cross the gate",
        }
    )
    monkeypatch.setattr(
        triage_gate,
        "_run_send",
        lambda argv: calls.append(argv) or (2, payload, "provider detail"),
    )

    with pytest.raises(triage_gate.GateError) as caught:
        triage_gate.execute_draft(draft, _approval())

    assert len(calls) == 1
    assert "provider detail" not in str(caught.value)
    stored = _stored_draft(tmp_path, draft)
    assert stored["status"] == "pending"
    assert stored["last_error"]["error_code"] == "attachment_upload_failed"
    assert triage_store.consecutive_send_failures(tmp_path / "triage.db") == 0


def test_approval_send_aborts_after_partial_upload_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    first = tmp_path / "partial-first.bin"
    second = tmp_path / "partial-second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    draft = _draft(first, second)
    calls: list[tuple[str, ...]] = []
    payload = json.dumps(
        {
            "status": "error",
            "error_code": "attachment_upload_failed",
            "stage": "attachment_upload",
            "retryable": True,
            "attachment_count": 1,
        }
    )
    monkeypatch.setattr(
        triage_gate,
        "_run_send",
        lambda argv: calls.append(argv) or (2, payload, ""),
    )

    with pytest.raises(triage_gate.GateError, match="attachment_upload_failed"):
        triage_gate.execute_draft(draft, _approval())

    assert len(calls) == 1
    assert _stored_draft(tmp_path, draft)["status"] == "pending"
    assert not (tmp_path / "gate" / "send-log.jsonl").exists()


def test_missing_attachment_is_validation_error_before_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    attachment = tmp_path / "missing.bin"
    attachment.write_bytes(b"will disappear")
    draft = _draft(attachment)
    attachment.unlink()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        triage_gate,
        "_run_send",
        lambda argv: calls.append(argv) or (0, "{}", ""),
    )

    with pytest.raises(triage_gate.GateError) as caught:
        triage_gate.execute_draft(draft, _approval())

    assert caught.value.exit_code == 2
    assert "attachment_invalid" in str(caught.value)
    assert calls == []
    stored = _stored_draft(tmp_path, draft)
    assert stored["status"] == "blocked"
    assert stored["last_error"] == {
        "error_code": "attachment_invalid",
        "stage": "validation",
        "retryable": False,
    }


def test_approved_send_preserves_attachment_reference_in_final_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    attachment = tmp_path / "reference.bin"
    attachment.write_bytes(b"reference")
    draft = _draft(attachment)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        triage_gate,
        "_run_send",
        lambda argv: calls.append(argv) or (0, _submitted_payload(draft, 1), ""),
    )

    triage_gate.execute_draft(draft, _approval())

    assert _attachment_values(calls[0]) == (str(attachment.resolve()),)
    send_record = json.loads(
        (tmp_path / "gate" / "send-log.jsonl").read_text(encoding="utf-8").strip()
    )
    assert send_record["attachment_manifest_sha256"] == draft["attachment_manifest_sha256"]
