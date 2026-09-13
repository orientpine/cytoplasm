"""위키 초안 저장 계약. 기존 gate 파사드의 설정과 주입 지점을 재사용한다."""
from __future__ import annotations

from importlib import import_module

import hashlib
import json
import os
import secrets
from pathlib import Path
import wiki_store


def _drafts_dir() -> Path:
    gate = import_module("wiki_gate")
    gate.GATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = gate.GATE_DIR / "drafts"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _draft_path(draft_id: str) -> Path:
    gate = import_module("wiki_gate")
    if not draft_id.isalnum():
        raise gate.GateError(f"잘못된 드래프트 id: {draft_id!r}", 3)
    return gate._drafts_dir() / f"{draft_id}.json"


def create_draft(
    action: str,
    slug: str,
    note_text: str,
    channel_id: str,
    *,
    summary: str | None = None,
) -> dict:
    gate = import_module("wiki_gate")
    wiki_store.parse_note(note_text)  # raises SchemaError before anything persists
    draft_id = secrets.token_hex(3)
    while gate._draft_path(draft_id).exists():
        draft_id = secrets.token_hex(3)
    record = {
        "action": action,
        "channel_id": channel_id,
        "created": wiki_store.utc_now(),
        "id": draft_id,
        "note_text": note_text,
        "sha256": hashlib.sha256(note_text.encode("utf-8")).hexdigest(),
        "slug": slug,
        "status": "pending",
    }
    # 요약은 선택 사항 — 없으면 레코드 모양은 예전과 완전히 동일하다.
    if isinstance(summary, str) and summary:
        record["summary"] = summary
    gate._write_json(gate._draft_path(draft_id), record)
    return record


def load_draft(draft_id: str) -> dict:
    gate = import_module("wiki_gate")
    path = gate._draft_path(draft_id)
    if not path.exists():
        raise gate.GateError(f"드래프트 없음: {draft_id}", 3)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "pending":
        raise gate.GateError(f"드래프트 {draft_id} 상태={record.get('status')} — pending 아님", 1)
    return record


def discard_draft(draft_id: str) -> None:
    gate = import_module("wiki_gate")
    path = gate._draft_path(draft_id)
    if not path.exists():
        raise gate.GateError(f"드래프트 없음: {draft_id}", 3)
    path.unlink()


def list_drafts() -> list[dict]:
    gate = import_module("wiki_gate")
    if not (gate._drafts_dir()).is_dir():
        return []
    records = []
    for path in sorted(gate._drafts_dir().glob("*.json")):
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def apply_draft(wiki_root: Path, draft: dict, approval_ref: str, method: str) -> Path:
    gate = import_module("wiki_gate")
    import wiki_binding

    channel_id = wiki_binding.persisted_channel_id(draft)
    if channel_id is None:
        draft_id = draft.get("id")
        if isinstance(draft_id, str):
            draft = gate.load_draft(draft_id)
            channel_id = wiki_binding.persisted_channel_id(draft)
    if channel_id is None:
        raise gate.GateError("저장에는 저장된 승인 바인딩이 필요함 — 거부", 1)
    draft = {
        **draft,
        "channel_id": channel_id,
        "kind": draft["kind"],
        "policy_version": draft["policy_version"],
        "surface": draft["surface"],
    }
    note_text = draft["note_text"]
    if hashlib.sha256(note_text.encode("utf-8")).hexdigest() != draft["sha256"]:
        raise gate.GateError("드래프트 내용 해시 불일치 — 저장 중단", 1)
    wiki_store.parse_note(note_text)  # re-validate at save time (fail-closed)
    wiki_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(wiki_root, 0o700)
    path = wiki_store.note_path(wiki_root, draft["slug"])
    if draft["action"] == "create" and path.exists():
        raise gate.GateError(f"이미 존재하는 노트: {draft['slug']} (수정은 --edit 사용)", 2)
    if draft["action"] == "edit" and not path.exists():
        raise gate.GateError(f"수정 대상 노트 없음: {draft['slug']}", 2)
    path.write_text(note_text, encoding="utf-8")
    path.chmod(0o600)
    draft = {**draft, "status": "saved", "approval_ref": approval_ref, "method": method}
    gate._write_json(gate._draft_path(draft["id"]), draft)
    gate._append_audit(draft, approval_ref, method)
    return path


def _append_audit(draft: dict, approval_ref: str, method: str) -> None:
    gate = import_module("wiki_gate")
    action = f"wiki.{draft['action']}"
    audit_method = "dm_text" if method == "owner_dm_reply" else method
    payload = {
        "action": action,
        "approval": {"channel": draft.get("channel_id", "dm"), "method": audit_method, "ref": approval_ref},
        "payload": {"note_sha256": draft["sha256"]},
        "target_id": f"note:{draft['slug']}",
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    record = {
        "action": action,
        "approval": payload["approval"],
        "hash": f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        "result": {"status": "saved"},
        "target_id": f"note:{draft['slug']}",
        "timestamp": wiki_store.utc_now(),
    }
    audit = gate.GATE_DIR / "audit.jsonl"
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    audit.chmod(0o600)


def _write_json(path: Path, record: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
