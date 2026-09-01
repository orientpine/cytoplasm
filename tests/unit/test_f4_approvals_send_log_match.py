"""F4 approvals↔send-log correlation: every send must carry an owner approval, and
every unmatched send must name *why* it is unmatched.

The inline audit in ``automation/final/f4_scope.sh`` reported one undifferentiated
``unmatched_sends`` count, so a checker matching gap and a genuine missing audit row
looked identical on the evidence line. It also accepted only ``reply_send`` and
``request_mail`` audit actions — ``mail.compose_send`` (added 2026-07-19, one day after
the matching rule was written) could never match even with a real owner ✅ behind it.

These tests pin both halves: the corrected matching rule, and the per-row reason. They
must never let an unapproved send pass — classification explains failures, it never
excuses them.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO / "automation" / "final" / "approvals_send_log_audit.py"
_SCOPE_SH = _REPO / "automation" / "final" / "f4_scope.sh"

OWNER = "owner-9999"


def _load_module() -> Any:
    """Load the audit exactly as the node does — a standalone file, not a package."""
    spec = importlib.util.spec_from_file_location("approvals_send_log_audit", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_module = _load_module()


# ------------------------------------------------------------------ fixtures


def _home(tmp_path: Path, *, owner_id: str | None = OWNER) -> Path:
    home = tmp_path / "home"
    interop = home / ".hermes" / "interop"
    interop.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {} if owner_id is None else {"owner_id": owner_id}
    _ = (interop / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _owner_approval(
    ref: str, *, method: str = "manual_reaction", owner: str = OWNER
) -> dict[str, Any]:
    """The record the external-effect gate writes when cha's ✅ is resolved."""
    return {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": ref,
            "method": method,
            "owner_id": owner,
        },
        "hash": "sha256:approval",
        "result": {"status": "approved"},
        "target_id": "tool:mailon_send:python3",
        "timestamp": "2026-08-01T00:00:00Z",
    }


def _send_audit(action: str, ref: str, *, method: str = "manual_reaction") -> dict[str, Any]:
    """The W0-6 audit mirror a gate appends next to each send-log row."""
    return {
        "action": action,
        "approval": {"channel": "approvals", "method": method, "ref": ref},
        "hash": "sha256:audit",
        "result": {"status": "sent"},
        "target_id": "mail:reply:masked",
        "timestamp": "2026-08-01T00:00:01Z",
    }


def _legacy_send_audit(action: str, ref: str, *, method: str = "manual_reaction") -> dict[str, Any]:
    """Legacy shape: the approval block carries ``message_id`` where current rows use ``ref``."""
    return {
        "action": action,
        "approval": {"channel": "approvals", "message_id": ref, "method": method},
        "result": {"status": "sent"},
        "target_id": "mail:reply:masked",
        "timestamp": "2026-07-17T00:00:01Z",
    }


def _send_log_row(
    ref: str, *, method: str = "manual_reaction", sha256: str = "0123456789abcdef"
) -> dict[str, Any]:
    return {
        "draft_id": "d1",
        "method": method,
        "ref": ref,
        "sensitive": False,
        "sha256": sha256,
        "status": "sent",
        "timestamp": "2026-08-01T00:00:02Z",
        "to_masked": "m***@example.invalid",
        "uid": "uid-1",
    }


def _run(
    tmp_path: Path,
    approvals: list[dict[str, Any]],
    send_log: list[dict[str, Any]],
    *,
    owner_id: str | None = OWNER,
    gate_dir: str = ".hermes/mail-triage",
) -> Any:
    home = _home(tmp_path, owner_id=owner_id)
    approvals_path = _write_jsonl(tmp_path / "approvals.jsonl", approvals)
    _ = _write_jsonl(home / gate_dir / "send-log.jsonl", send_log)
    return audit_module.audit(approvals_path, home)


def _reasons(result: Any) -> list[str]:
    return [row.reason for row in result.unmatched]


# ------------------------------------------------------------------ matching


def test_manual_reaction_reply_with_owner_approval_matches(tmp_path: Path) -> None:
    # Given: a mail reply sent under cha's ✅, with both approvals.jsonl rows present.
    ref = "reaction:msg-reply"

    # When: the audit correlates the send-log row against the approval ledger.
    result = _run(
        tmp_path,
        [_owner_approval(ref), _send_audit("mail.reply_send", ref)],
        [_send_log_row(ref)],
    )

    # Then: it matches, and the audit passes.
    assert result.unmatched == ()
    assert result.sent_records == 1
    assert result.exit_code == 0


def test_manual_reaction_compose_with_owner_approval_matches(tmp_path: Path) -> None:
    # Given: an owner-approved compose send — its audit action is `mail.compose_send`,
    # which the pre-fix suffix rule (`reply_send`/`request_mail`) could never accept.
    ref = "reaction:msg-compose"

    # When: the audit runs.
    result = _run(
        tmp_path,
        [_owner_approval(ref), _send_audit("mail.compose_send", ref)],
        [_send_log_row(ref)],
    )

    # Then: a genuine owner approval matches instead of counting as a mismatch.
    assert _reasons(result) == []
    assert result.exit_code == 0


def test_budget_request_mail_with_owner_approval_matches(tmp_path: Path) -> None:
    # Given: the budget gate's send, whose audit action is `budget.request_mail`.
    ref = "reaction:msg-budget"

    # When / Then: the pre-existing matching rule keeps working.
    result = _run(
        tmp_path,
        [_owner_approval(ref), _send_audit("budget.request_mail", ref)],
        [_send_log_row(ref)],
        gate_dir=".hermes/budget-gate",
    )
    assert result.unmatched == ()
    assert result.exit_code == 0


def test_legacy_audit_row_keyed_by_message_id_matches(tmp_path: Path) -> None:
    # Given: a legacy audit row that names the approval as `message_id`, not `ref`.
    ref = "reaction:msg-legacy"

    # When: the audit runs against a send-log row keyed on the same reference.
    result = _run(
        tmp_path,
        [_owner_approval(ref), _legacy_send_audit("mail.reply_send", ref)],
        [_send_log_row(ref)],
    )

    # Then: the legacy shape correlates rather than being reported as a hole.
    assert result.unmatched == ()
    assert result.exit_code == 0


def test_legacy_audit_row_without_owner_approval_still_fails(tmp_path: Path) -> None:
    # Given: the same legacy audit row, but no owner approval behind it.
    ref = "reaction:msg-legacy-unapproved"

    # When: the audit runs.
    result = _run(tmp_path, [_legacy_send_audit("mail.reply_send", ref)], [_send_log_row(ref)])

    # Then: tolerating the legacy shape must not authorize an unapproved send.
    assert _reasons(result) == [audit_module.REASON_APPROVAL_MISSING]
    assert result.exit_code == 1


# ------------------------------------------------------------------ reasons


def test_send_without_owner_approval_is_reported_as_approval_missing(tmp_path: Path) -> None:
    # Given: a send whose audit mirror exists but which no owner ✅ ever authorized.
    ref = "reaction:msg-evil"

    # When: the audit runs.
    result = _run(tmp_path, [_send_audit("mail.compose_send", ref)], [_send_log_row(ref)])

    # Then: the invariant holds — the send is a failure, named as a missing approval.
    assert _reasons(result) == [audit_module.REASON_APPROVAL_MISSING]
    assert result.unmatched[0].ref == ref
    assert result.exit_code == 1


def test_non_owner_approval_does_not_authorize_a_send(tmp_path: Path) -> None:
    # Given: an approval record bound to somebody other than the owner.
    ref = "reaction:msg-nonowner"

    # When: the audit runs.
    result = _run(
        tmp_path,
        [
            _owner_approval(ref, owner="intruder-1"),
            _send_audit("mail.reply_send", ref),
        ],
        [_send_log_row(ref)],
    )

    # Then: owner binding still decides — this is an approval hole, not a match.
    assert _reasons(result) == [audit_module.REASON_APPROVAL_MISSING]
    assert result.exit_code == 1


def test_approved_send_with_no_audit_row_is_reported_as_send_log_row_missing(
    tmp_path: Path,
) -> None:
    # Given: a genuine owner approval, but approvals.jsonl carries no mirror row for it.
    ref = "reaction:msg-unlogged"

    # When: the audit runs.
    result = _run(tmp_path, [_owner_approval(ref)], [_send_log_row(ref)])

    # Then: the row is named as a genuinely missing send-log entry (branch (b)).
    assert _reasons(result) == [audit_module.REASON_SEND_LOG_ROW_MISSING]
    assert result.exit_code == 1


def test_unrecognized_send_action_is_reported_as_method_not_matched(tmp_path: Path) -> None:
    # Given: an approved send whose audit row uses an action the rule does not know.
    ref = "reaction:msg-unknown-action"

    # When: the audit runs.
    result = _run(
        tmp_path,
        [_owner_approval(ref), _send_audit("mail.dispatch_send", ref)],
        [_send_log_row(ref)],
    )

    # Then: it is named as a checker matching gap (branch (a)) — and still fails.
    assert _reasons(result) == [audit_module.REASON_METHOD_NOT_MATCHED]
    assert result.exit_code == 1


def test_audit_row_recorded_under_another_method_is_reported_as_method_not_matched(
    tmp_path: Path,
) -> None:
    # Given: the send-log row says manual_reaction while its mirror says injection.
    ref = "reaction:msg-method-skew"

    # When: the audit runs.
    result = _run(
        tmp_path,
        [
            _owner_approval(ref),
            _send_audit("mail.reply_send", ref, method="signed_injection_e2e"),
        ],
        [_send_log_row(ref)],
    )

    # Then: the mismatch is attributed to the method, not to a missing row.
    assert _reasons(result) == [audit_module.REASON_METHOD_NOT_MATCHED]
    assert result.exit_code == 1


def test_every_reason_is_one_of_the_declared_reasons(tmp_path: Path) -> None:
    # Given: one send per failure class in a single ledger.
    approvals = [
        _owner_approval("reaction:a"),
        _owner_approval("reaction:b"),
        _send_audit("mail.dispatch_send", "reaction:b"),
        _send_audit("mail.reply_send", "reaction:c"),
    ]
    send_log = [
        _send_log_row("reaction:a", sha256="aaaaaaaaaaaaaaaa"),
        _send_log_row("reaction:b", sha256="bbbbbbbbbbbbbbbb"),
        _send_log_row("reaction:c", sha256="cccccccccccccccc"),
    ]

    # When: the audit runs.
    result = _run(tmp_path, approvals, send_log)

    # Then: each unmatched row is classified, with no undifferentiated bucket.
    assert sorted(_reasons(result)) == sorted(
        [
            audit_module.REASON_SEND_LOG_ROW_MISSING,
            audit_module.REASON_METHOD_NOT_MATCHED,
            audit_module.REASON_APPROVAL_MISSING,
        ]
    )
    assert result.exit_code == 1


# ------------------------------------------------------- injected e2e rows


def test_signed_injection_e2e_injected_rows_are_excluded_from_sent_records(
    tmp_path: Path,
) -> None:
    # Given: an E2E-injected send-log row, which is not a production send.
    result = _run(
        tmp_path,
        [],
        [_send_log_row("injected:e2e-1", method="signed_injection_e2e")],
    )

    # Then: it is counted separately and never treated as an unmatched production send.
    assert result.injected_test_records == 1
    assert result.sent_records == 0
    assert result.unmatched == ()
    assert result.exit_code == 0


def test_signed_injection_method_without_injected_ref_still_needs_approval(
    tmp_path: Path,
) -> None:
    # Given: the injection method claimed on a row that is not an injected reference.
    ref = "reaction:msg-pretend-injected"

    # When: the audit runs.
    result = _run(tmp_path, [], [_send_log_row(ref, method="signed_injection_e2e")])

    # Then: the injected carve-out cannot be borrowed to skip the approval requirement.
    assert result.injected_test_records == 0
    assert _reasons(result) == [audit_module.REASON_APPROVAL_MISSING]
    assert result.exit_code == 1


def test_injected_row_is_matched_when_it_carries_a_real_approval(tmp_path: Path) -> None:
    # Given: an e2e approval + audit pair recorded for an injected reference.
    ref = "injected:e2e-2"

    # When / Then: the row stays in the injected bucket regardless of the ledger.
    result = _run(
        tmp_path,
        [
            _owner_approval(ref, method="signed_injection_e2e"),
            _send_audit("mail.reply_send", ref, method="signed_injection_e2e"),
        ],
        [_send_log_row(ref, method="signed_injection_e2e")],
    )
    assert result.injected_test_records == 1
    assert result.exit_code == 0


def test_non_sent_send_log_rows_are_ignored(tmp_path: Path) -> None:
    # Given: a blocked draft row that never produced an external effect.
    row = _send_log_row("reaction:msg-blocked") | {"status": "blocked"}

    # When / Then: only terminal sends are audited.
    result = _run(tmp_path, [], [row])
    assert result.sent_records == 0
    assert result.unmatched == ()
    assert result.exit_code == 0


# ------------------------------------------------------------- fail-closed


def test_missing_approvals_log_is_an_error_not_a_pass(tmp_path: Path) -> None:
    # Given: the approvals ledger cannot be read.
    home = _home(tmp_path)
    _ = _write_jsonl(home / ".hermes" / "mail-triage" / "send-log.jsonl", [])

    # When / Then: the audit fails closed instead of reporting zero unmatched sends.
    with pytest.raises(audit_module.AuditInputError):
        _ = audit_module.audit(tmp_path / "absent.jsonl", home)


def test_malformed_approvals_row_is_an_error_not_a_skip(tmp_path: Path) -> None:
    # Given: one corrupted line in the approvals ledger.
    home = _home(tmp_path)
    approvals = tmp_path / "approvals.jsonl"
    _ = approvals.write_text('{"action":"external_effect.approval"}\nnot-json\n', encoding="utf-8")
    _ = _write_jsonl(home / ".hermes" / "mail-triage" / "send-log.jsonl", [])

    # When / Then: an unreadable ledger can never be scored as a clean audit.
    with pytest.raises(audit_module.AuditInputError):
        _ = audit_module.audit(approvals, home)


def test_malformed_send_log_row_is_an_error_not_a_skip(tmp_path: Path) -> None:
    # Given: a corrupted send-log row that could be hiding a real send.
    home = _home(tmp_path)
    approvals = _write_jsonl(tmp_path / "approvals.jsonl", [])
    send_log = home / ".hermes" / "mail-triage" / "send-log.jsonl"
    send_log.parent.mkdir(parents=True, exist_ok=True)
    _ = send_log.write_text("{oops\n", encoding="utf-8")

    # When / Then: fail closed.
    with pytest.raises(audit_module.AuditInputError):
        _ = audit_module.audit(approvals, home)


def test_missing_owner_id_is_an_error_not_a_pass(tmp_path: Path) -> None:
    # Given: the interop config carries no owner identity to bind approvals to.
    with pytest.raises(audit_module.AuditInputError):
        _ = _run(tmp_path, [], [_send_log_row("reaction:msg-1")], owner_id=None)


def test_main_returns_nonzero_when_an_input_is_unreadable(tmp_path: Path) -> None:
    # Given: a run whose interop config is absent entirely.
    home = tmp_path / "home"
    home.mkdir()
    approvals = _write_jsonl(tmp_path / "approvals.jsonl", [])

    # When: the module is driven through its CLI entry point.
    exit_code = audit_module.main([str(approvals), str(home)])

    # Then: it exits non-zero — an unreadable input is never a PASS.
    assert exit_code != 0


# --------------------------------------------------------- evidence contract


def test_report_lines_preserve_the_recorded_evidence_contract(tmp_path: Path) -> None:
    # Given: one matched send, one injected row, and one unapproved send.
    approvals = [
        _owner_approval("reaction:ok"),
        _send_audit("mail.compose_send", "reaction:ok"),
    ]
    send_log = [
        _send_log_row("reaction:ok"),
        _send_log_row("injected:e2e-3", method="signed_injection_e2e"),
        _send_log_row("reaction:evil", sha256="eeeeeeeeeeeeeeeeeeee"),
    ]

    # When: the audit renders the lines recorded as `approvals-send-log` evidence.
    result = _run(tmp_path, approvals, send_log)
    lines = result.report_lines()

    # Then: the historical counters survive, per-reason counts are added, and each
    # unmatched detail line names its reason.
    assert lines[:5] == (
        "owner_approved_records=1",
        "send_logged_records=1",
        "sent_records=2",
        "injected_test_records=1",
        "unmatched_sends=1",
    )
    assert f"unmatched_{audit_module.REASON_APPROVAL_MISSING.replace('-', '_')}=1" in lines
    detail = [line for line in lines if line.startswith("unmatched ref=")]
    assert detail == [
        "unmatched ref='reaction:evil' method='manual_reaction' "
        f"sha256_prefix=eeeeeeeeeeee reason={audit_module.REASON_APPROVAL_MISSING}"
    ]


# ------------------------------------------------------------ shell wiring


def test_f4_scope_delegates_the_audit_to_the_module() -> None:
    # Given: the F4 scope script, which must not carry the audit logic inline.
    script = _SCOPE_SH.read_text(encoding="utf-8")

    # Then: it feeds the module to the node and keeps its recorded evidence name.
    assert "approvals_send_log_audit.py" in script
    assert "record approvals-send-log check_approvals" in script
    assert "owner_approved = set()" not in script
    assert 'rglob("send-log.jsonl")' not in script


def test_f4_scope_shell_parses() -> None:
    # Given / When: the script is syntax-checked without ever executing it (it ssh-es
    # to a production node).
    result = subprocess.run(
        ("bash", "-n", str(_SCOPE_SH)), capture_output=True, text=True, check=False
    )

    # Then: the rewrite is syntactically clean.
    assert result.returncode == 0, result.stderr
