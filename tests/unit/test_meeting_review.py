"""Synthetic review receipts only; never use a real transcript or approval digest."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/meeting/scripts"))
import meeting_cli  # noqa: E402
import meeting_gate  # noqa: E402
import meeting_review  # noqa: E402


def test_keyword_gate_stays_conservative():
    rules = meeting_gate.load_rules(ROOT / "configs/sensitivity-rules.yaml")
    assert meeting_gate.evaluate("청구항 검토", rules).sensitive


def _receipt(tmp_path, monkeypatch, material="synthetic material"):
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    directory = tmp_path / "receipts"
    directory.mkdir()
    monkeypatch.setattr(meeting_review, "RECEIPT_DIR", directory)
    path = directory / f"{digest}.json"
    data = {"version": 1, "gate_sha256": digest, "decision": "non-sensitive",
            "owner": "operator", "reason": "Reviewed sample", "reviewed_on": "2026-10-07",
            "publication_scope": "meeting-downstream"}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


def test_changed_gate_material_does_not_reuse_receipt(tmp_path, monkeypatch):
    _receipt(tmp_path, monkeypatch)
    monkeypatch.setattr(meeting_review, "_trusted", lambda path, fd: True)
    assert not meeting_review.approved("synthetic material plus changed evidence")


@pytest.mark.parametrize("update", [
    {"gate_sha256": "0" * 64}, {"publication_scope": "owner-drive"},
    {"reason": ""}, {"owner": ""}, {"reviewed_on": "2099-01-01"},
    {"decision": "sensitive"}, {"version": True},
])
def test_malformed_or_mismatched_receipt_is_denied(tmp_path, monkeypatch, update):
    path, data = _receipt(tmp_path, monkeypatch)
    monkeypatch.setattr(meeting_review, "_trusted", lambda path, fd: True)
    data.update(update)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not meeting_review.approved("synthetic material")


def test_untrusted_agent_owned_receipt_is_denied(tmp_path, monkeypatch):
    _receipt(tmp_path, monkeypatch)
    assert not meeting_review.approved("synthetic material")


def test_symlink_receipt_is_denied(tmp_path, monkeypatch):
    path, _ = _receipt(tmp_path, monkeypatch)
    target = tmp_path / "target.json"
    path.rename(target)
    path.symlink_to(target)
    monkeypatch.setattr(meeting_review, "_trusted", lambda path, fd: True)
    assert not meeting_review.approved("synthetic material")


def test_trusted_check_rejects_writable_ancestor(tmp_path, monkeypatch):
    path, _ = _receipt(tmp_path, monkeypatch)
    with path.open("rb") as handle:
        assert not meeting_review._trusted(path, handle.fileno())


def test_matching_owner_receipt_preserves_sensitive_llm_route_and_publishes(tmp_path, monkeypatch, capsys):
    material = "회의에서는 청구항 표현을 살펴보고 다음 주 일정과 담당자를 정리했습니다."  # synthetic
    source = tmp_path / "source.md"
    source.write_text(material, encoding="utf-8")
    monkeypatch.setenv("MEETING_RULES_FILE", str(ROOT / "configs/sensitivity-rules.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(ROOT / "skills/meeting/prompts/meeting-extraction-v6.md"))
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "milestones.yaml"))
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(ROOT / "skills"))
    monkeypatch.setattr(meeting_cli.meeting_reference, "collect", lambda _: ())
    response = ROOT / "skills/meeting/fixtures/recorded-clean.json"
    gate_input = "\n".join((material, "", ""))
    digest = hashlib.sha256(gate_input.encode("utf-8")).hexdigest()
    receipts = tmp_path / "reviews"
    receipts.mkdir()
    monkeypatch.setattr(meeting_review, "RECEIPT_DIR", receipts)
    monkeypatch.setattr(meeting_review, "_trusted", lambda path, fd: True)
    (receipts / f"{digest}.json").write_text(json.dumps({
        "version": 1, "gate_sha256": digest, "decision": "non-sensitive",
        "owner": "operator", "reason": "Reviewed synthetic keyword match",
        "reviewed_on": "2026-10-07", "publication_scope": "meeting-downstream",
    }), encoding="utf-8")
    published = []
    monkeypatch.setattr(meeting_cli, "_publish_note", lambda *args, **kwargs: published.append(kwargs))
    routed = []
    original_extract = meeting_cli.meeting_llm.extract
    def check_route(*args, **kwargs):
        routed.append(kwargs["sensitive"])
        return original_extract(*args, **kwargs)
    monkeypatch.setattr(meeting_cli.meeting_llm, "extract", check_route)
    rc = meeting_cli.main(["ingest", "--file", str(source), "--recorded-response", str(response), "--offline"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["sensitive"] is False
    log = next((tmp_path / "logs").glob("*.jsonl")).read_text(encoding="utf-8")
    assert json.loads(log.splitlines()[-1])["review_sha256"] == digest
    assert routed == [True], "receipt must not downgrade pre-LLM routing"
    assert published and published[0]["sensitive"] is False
    note = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")
    assert "patent-sensitive" not in note
    assert material in note.split("### C. 원문 전사본", 1)[-1]
