"""Sensitive card wire snapshots, captured before the envelope migration."""
from __future__ import annotations

import sys
import builtins
import importlib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/wiki/scripts"))
sys.path.insert(0, str(ROOT / "skills/patent-prep"))

wiki_gate = importlib.import_module("wiki_gate")
patent_export = importlib.import_module("scripts.patent_export")
patent_export_gate = importlib.import_module("scripts.patent_export_gate")
pm = importlib.import_module("scripts.patent_export_manifest")

WIKI_V1 = "저장 abc123 sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
WIKI_SUMMARY_V1 = (
    "저장 abc123 sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
    "private summary"
)
PATENT_V1 = (
    "PATENT EXPORT APPROVAL REQUEST\n"
    "slug: abc123\n"
    "sha256: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
    "dest_folder_id: folder-test\n"
    "expiry_ts: 1800000000\n"
    "mode=enc\n"
    "이 메시지에 ✅ 실행 / ⛔ 취소\n"
)
NOTICE_V1 = "Patent export completed: https://example.invalid/private"
WIKI_V2 = (
    "대상: abc123 (abc123)\n"
    "사실: sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb (승인 요청; 만료: 기한 없음)\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 ✅ 실행 / ⛔ 취소; 다음: 승인 시 저장\n"
    "되돌리기: 해당 없음; 취소 시: 저장하지 않음"
)
PATENT_V2 = (
    "대상: abc123 (abc123)\n"
    "사실: 특허 반출 (승인 요청; 만료: 2027-01-15T08:00:00+00:00)\n"
    "sha256: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
    "dest_folder_id: folder-test\n"
    "expiry_ts: 1800000000\n"
    "mode=enc\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 ✅ 실행 / ⛔ 취소; 다음: 승인 시 반출\n"
    "되돌리기: 해당 없음; 취소 시: 반출하지 않음"
)


def draft() -> dict[str, str]:
    return {"id": "abc123", "sha256": "b" * 64}


def manifest() -> DataclassInstance:
    return pm.Manifest(
        slug="abc123", plaintext_sha256="sha256:" + "a" * 64,
        dest_folder_id="folder-test", mode="enc", expiry_ts=1800000000,
        nonce="abcdef0123456789", state=pm.State.PENDING, message_id=None,
        created_ts=1799996400, approval_ts=None, approval_thread_id=None,
        kind="patent-export", surface="agent-chat-thread", channel_id="333", policy_version=9,
    )


@pytest.mark.parametrize("summary", [False, True])
def test_wiki_legacy_bytes_when_replaying(summary: bool) -> None:
    # Given
    record = draft() | ({"summary": "private summary"} if summary else {})
    # When
    content = wiki_gate.confirm_text(record, surface="owner-dm")
    # Then
    assert content == (WIKI_SUMMARY_V1 if summary else WIKI_V1)


def test_patent_legacy_bytes_when_replaying() -> None:
    # Given
    record = manifest()
    # When
    content = patent_export.render_approval(record)
    # Then
    assert content == PATENT_V1
    assert patent_export_gate.approval_binding_matches(record, content)


def test_patent_notice_legacy_bytes_when_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: old runtime without the envelope contract.
    original_import = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "automation.interop.owner_message":
            raise ImportError(name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    sent = []
    def api(method, path, payload):
        sent.append((method, path, payload))
        return {"id": "222"}
    monkeypatch.setattr(patent_export_gate, "_api", api)
    # When
    receipt = patent_export_gate.dm_owner("111", NOTICE_V1)
    # Then
    assert receipt == "222"
    assert sent == [("POST", "/channels/111/messages", {"content": NOTICE_V1})]


@pytest.mark.parametrize("producer", ["wiki", "patent"])
def test_card_fields_when_rendering_new_masked_record(producer: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: every sensitive optional value must be ignored, even in a direct message.
    from automation.interop import owner_message as om
    captured = []
    original = om.render
    def capture(message, *, destination):
        captured.append((message, destination))
        return original(message, destination=destination)
    monkeypatch.setattr(om, "render", capture)
    # When
    if producer == "wiki":
        content = wiki_gate.confirm_text(draft() | {
            "render_version": 2, "summary": "PRIVATE TITLE https://example.invalid/private",
            "note_text": "PRIVATE BODY", "title": "PRIVATE TITLE", "surface": "owner-dm",
        })
        expected = om.OwnerMessage(
            subject_key="abc123", subject="abc123",
            fact="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            location=om.Ref(scope="self"), owner=om.Action("react", om.Ref(scope="self"), "✅ 실행 / ⛔ 취소"),
            agent_next="승인 시 저장", recovery="not_applicable",
            detail=om.Approval(None, "저장하지 않음"),
        )
        expected_content = WIKI_V2
    else:
        record = SimpleNamespace(**(asdict(manifest()) | {
            "render_version": 2, "title": "PRIVATE TITLE", "body": "PRIVATE BODY",
            "url": "https://example.invalid/private",
        }))
        content = patent_export.render_approval(record)
        expected = om.OwnerMessage(
            subject_key="abc123", subject="abc123", fact="특허 반출",
            location=om.Ref(scope="self"), owner=om.Action("react", om.Ref(scope="self"), "✅ 실행 / ⛔ 취소"),
            agent_next="승인 시 반출", recovery="not_applicable",
            detail=om.Approval(datetime(2027, 1, 15, 8, tzinfo=UTC), "반출하지 않음"),
        )
        expected_content = PATENT_V2
        assert content.splitlines()[2:6] == [
            "sha256: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "dest_folder_id: folder-test", "expiry_ts: 1800000000", "mode=enc",
        ]
        assert patent_export_gate.approval_binding_matches(record, content)
    # Then: whole envelopes and shipped bytes are independent literal oracles.
    assert content == expected_content
    assert captured == [(expected, om.Ref(scope="self"))]
    assert "discord.com" not in content
    assert "https://" not in content
    assert "PRIVATE TITLE" not in content
    assert "PRIVATE BODY" not in content


def test_patent_notice_fields_when_completion_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    from automation.interop import owner_message as om
    captured, sent = [], []
    original = om.render
    def capture(message, *, destination):
        captured.append((message, destination))
        return original(message, destination=destination)
    def api(method, path, payload):
        sent.append((method, path, payload))
        return {"id": "222"}
    monkeypatch.setattr(om, "render", capture)
    monkeypatch.setattr(patent_export_gate, "_api", api)
    # When
    receipt = patent_export_gate.dm_owner("111", NOTICE_V1)
    # Then
    assert receipt == "222"
    assert sent == [("POST", "/channels/111/messages", {"content": (
        "대상: 특허 반출 (patent-export)\n"
        "사실: 반출 완료 (실행 완료)\n"
        "위치: 해당 없음\n"
        "인계: 소유자: 조치 없음; 다음: 추가 반출 없음\n"
        "되돌리기: 해당 없음"
    )})]
    assert captured == [(om.OwnerMessage(
        subject_key="patent-export", subject="특허 반출", fact="반출 완료",
        location=om.Ref(scope="none"), owner=om.Action("none"),
        agent_next="추가 반출 없음", recovery="not_applicable", detail=om.Result("executed"),
    ), om.Ref(scope="channel", space="dm", channel_id="111"))]
    assert "https://" not in sent[0][2]["content"]
