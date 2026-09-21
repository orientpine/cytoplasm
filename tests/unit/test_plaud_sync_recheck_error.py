"""A temporary cloud outage must not replace the recording failure diagnosis."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from automation.plaud_sync.model import PlaudSyncState, serialize_record
from automation.plaud_sync.store import save_state
from automation.plaud_sync.transcribe import TranscribeError, process
from tests.unit.test_plaud_sync_transcribe import _RECORD
from tests.unit.test_plaud_sync_transcribe_policy import CountingEffects, NOW


@pytest.mark.parametrize("method", ["fetch_summary", "fetch_transcript", "fetch_source"])
@pytest.mark.parametrize("counted", [False, True])
def test_recheck_preserves_recording_reason_when_cloud_error_is_uncounted(
    monkeypatch: pytest.MonkeyPatch, method: str, counted: bool,
) -> None:
    # Given
    effects = CountingEffects()
    reason = "rc=5 unsupported-format"
    record = replace(_RECORD, last_block_reason=reason, transcribe_attempts=2,
                     next_transcribe_at=(NOW + timedelta(hours=1)).isoformat())
    error = TranscribeError(f"{method}: PlaudMcpError", counted=counted)

    def unavailable(recording_id: str) -> str:
        raise error

    monkeypatch.setattr(effects, method, unavailable)
    # When
    outcome = process(record, effects=effects, max_attempts=2, now=lambda: NOW)
    # Then
    after = effects.commits[0][1]
    assert outcome == "retry"
    assert after.last_block_reason == (error.reason if counted else reason)
    if not counted:
        assert serialize_record(after)["last_recheck_error"] == error.reason
    assert after.transcribe_attempts == 2 and after.next_transcribe_at == record.next_transcribe_at
    assert effects.labels == []


def test_status_cli_exposes_both_errors_when_cloud_recheck_failed(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "state.json"
    record = replace(_RECORD, last_block_reason="rc=5 unsupported-format")
    save_state(path, PlaudSyncState(1, None, {record.recording_id: record}))
    payload = json.loads(path.read_text())
    payload["records"][record.recording_id]["last_recheck_error"] = "get_note: PlaudMcpError"
    path.write_text(json.dumps(payload))
    cli = Path(__file__).resolve().parents[2] / "skills/plaud/scripts/plaud_cli.py"
    # When
    result = subprocess.run([sys.executable, str(cli), "status", "--state", str(path), "--json"],
                            env={"PATH": os.defpath, "HOME": str(tmp_path)},
                            capture_output=True, text=True, timeout=30)
    # Then
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)["transcribing"][0]
    assert row["reason"] == record.last_block_reason
    assert row["last_recheck_error"] == "get_note: PlaudMcpError"
