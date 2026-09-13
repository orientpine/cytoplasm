"""Procurement review transport contracts without Discord or Drive network calls."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final
from unittest.mock import Mock

import pytest

from automation import drive_outputs

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "procurement" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
from skills.procurement.scripts import procure_review as pr  # noqa: E402


# Captured from 2ba312691 send_review; never derive this byte pin from a live builder.
_LEGACY_WITHOUT_LINK: Final = (
    "📄 서류 초안 검토 요청: `draft.hwpx`\nreview\nnotes\n(Drive 링크: )\n"
    "검토 후 **제출은 cha가 직접** 해주세요 — 이 스킬은 어디에도 제출하지 않습니다."
)


@pytest.mark.parametrize("scenario", [(1, "attach"), (0, "drive-link")])
def test_transport_keeps_route_and_receipt_when_review_is_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: tuple[int, str],
) -> None:
    # Given a real artifact and narrow outbound boundaries.
    limit, mode = scenario
    file = tmp_path / "draft.hwpx"
    file.write_bytes(b"x")
    monkeypatch.delenv("PROCURE_DISCORD_STUB", raising=False)
    monkeypatch.setenv("PROCURE_DM_MAX_BYTES", str(limit))
    monkeypatch.setattr(pr, "_notice_channel", lambda: "111")
    publish = Mock(return_value=drive_outputs.PublishResult(("https://drive.example/draft",), "created", "folder"))
    monkeypatch.setattr(drive_outputs, "publish_best_effort", publish)
    post = Mock(return_value={"id": "222"})
    attach = Mock(return_value={"id": "333"})
    monkeypatch.setattr(pr, "_api", post)
    monkeypatch.setattr(pr, "_post_attachment", attach)
    # When
    receipt = pr.send_review(file, "검토")
    # Then: route choice and rc-facing receipt are independent of notice wording.
    if mode == "attach":
        assert attach.call_args.args[:2] == ("111", file)
        post.assert_not_called()
        publish.assert_not_called()
        assert receipt == "REVIEW-DM-SENT message=333 mode=attach size=1"
    else:
        assert post.call_args.args[:2] == ("POST", "/channels/111/messages")
        assert set(post.call_args.args[2]) == {"content"}
        attach.assert_not_called()
        publish.assert_called_once_with("procurement", "draft", [(file, "draft")])
        assert receipt == "REVIEW-DM-SENT message=222 mode=drive-link size=1"


def test_stub_records_review_when_drive_import_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    file = tmp_path / "draft.hwpx"
    file.write_bytes(b"x")
    stub = tmp_path / "stub"
    stub.mkdir()
    monkeypatch.setenv("PROCURE_DISCORD_STUB", str(stub))
    monkeypatch.setenv("PROCURE_DM_MAX_BYTES", "0")
    monkeypatch.setattr(drive_outputs, "publish_best_effort", Mock(side_effect=ImportError("unavailable")))
    channel = Mock(side_effect=AssertionError("stub must not resolve Discord"))
    monkeypatch.setattr(pr, "_notice_channel", channel)
    # When
    receipt = pr.send_review(file, "검토")
    # Then
    records = list(stub.iterdir())
    assert len(records) == 1
    assert receipt == f"REVIEW-DM-SENT message=stub:{records[0].name} mode=drive-link size=1"
    record = json.loads(records[0].read_text())
    assert (record["mode"], record["size"], record["file"]) == ("drive-link", 1, file.name)
    channel.assert_not_called()
    assert file.name in record["content"].splitlines()[2]
    assert "https://" not in record["content"]


@pytest.mark.parametrize("link", ["https://drive.example/draft", ""])
def test_document_ref_reaches_renderer_when_drive_result_varies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link: str,
) -> None:
    # Given the actual renderer wrapped only to inspect its typed input.
    from automation.interop import owner_message as om

    file = tmp_path / "draft.hwpx"
    file.write_bytes(b"x")
    stub = tmp_path / "stub"
    stub.mkdir()
    monkeypatch.setenv("PROCURE_DISCORD_STUB", str(stub))
    monkeypatch.setenv("PROCURE_DM_MAX_BYTES", "0")
    monkeypatch.setattr(drive_outputs, "publish_best_effort", Mock(
        return_value=drive_outputs.PublishResult((link,) if link else (), "created", "folder"),
    ))
    renderer = Mock(wraps=om.render)
    monkeypatch.setattr(om, "render", renderer)
    # When
    pr.send_review(file, "검토")
    # Then
    assert renderer.call_count == 1
    envelope = renderer.call_args.args[0]
    assert envelope.subject_key == file.name
    assert envelope.location == om.Ref(scope="resource", url=link or None, search=("문서 검색", file.name))
    assert renderer.call_args.kwargs["destination"] == om.Ref(scope="none")
    content = json.loads(next(stub.iterdir()).read_text())["content"]
    assert (link or file.name) in content.splitlines()[2]
    assert len(content.splitlines()) == 5


@pytest.mark.parametrize("scenario", [
    pytest.param((True, True), id="renderer-refusal"),
    pytest.param((False, True), id="import-unavailable"),
    pytest.param((True, False), id="renderer-refusal-publisher-import-error"),
    pytest.param((False, False), id="import-unavailable-publisher-import-error"),
])
def test_legacy_bytes_are_sent_when_envelope_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: tuple[bool, bool],
) -> None:
    # Given both capability fallbacks crossed with absent-link/publisher-import failure.
    available, publisher_available = scenario
    from automation.interop import owner_message as om

    file = tmp_path / "draft.hwpx"
    file.write_bytes(b"x")
    monkeypatch.setenv("PROCURE_DM_MAX_BYTES", "0")
    monkeypatch.delenv("PROCURE_DISCORD_STUB", raising=False)
    monkeypatch.setattr(pr, "_notice_channel", lambda: "111")
    monkeypatch.setattr(drive_outputs, "publish_best_effort", Mock(
        return_value=None, side_effect=None if publisher_available else ImportError("unavailable"),
    ))
    if available:
        monkeypatch.setattr(om, "render", Mock(side_effect=om.OwnerMessageError(detail="message.fact")))
    else:
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    post = Mock(return_value={"id": "222"})
    monkeypatch.setattr(pr, "_api", post)
    # When
    pr.send_review(file, "review\nnotes")
    # Then
    assert post.call_args.args[2]["content"].encode() == _LEGACY_WITHOUT_LINK.encode("utf-8")


def test_attachment_carries_search_locator_when_file_has_no_drive_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a real artifact and channel id; rendering must not infer a DM space.
    from automation.interop import owner_message as om

    file = tmp_path / "draft.hwpx"
    file.write_bytes(b"x")
    monkeypatch.setenv("PROCURE_DM_MAX_BYTES", "1")
    monkeypatch.delenv("PROCURE_DISCORD_STUB", raising=False)
    monkeypatch.setattr(pr, "_notice_channel", lambda: "111")
    renderer = Mock(wraps=om.render)
    monkeypatch.setattr(om, "render", renderer)
    attach = Mock(return_value={"id": "222"})
    monkeypatch.setattr(pr, "_post_attachment", attach)
    # When
    pr.send_review(file, "review")
    # Then
    assert renderer.call_args.kwargs["destination"] == om.Ref(scope="channel", channel_id="111")
    assert renderer.call_args.args[0].location == om.Ref(scope="resource", search=("문서 검색", file.name))
    content = attach.call_args.args[2]
    assert file.name in content.splitlines()[2]
    assert "https://" not in content


@pytest.mark.parametrize("filename", ["draft.hwpx", ""])
def test_cli_reports_delivery_or_input_error_when_review_is_requested(tmp_path: Path, filename: str) -> None:
    # Given an isolated CLI runtime with Drive disabled and the real Discord stub.
    file = tmp_path / "draft.hwpx"
    file.write_bytes(b"x")
    stub = tmp_path / "stub"
    stub.mkdir()
    root = _SCRIPTS.parents[2]
    environment = {
        **os.environ, "HOME": str(tmp_path), "DRIVE_PUBLISH_ENABLED": "0",
        "PROCURE_DISCORD_STUB": str(stub), "PROCURE_DM_MAX_BYTES": "0",
        "AUTOPHAGY_SKILL_LIVE_ROOT": str(tmp_path / "live"),
        "PROCURE_AUDIT_LOG": str(tmp_path / "audit.log"),
        "PYTHONPATH": os.pathsep.join((str(root), str(_SCRIPTS))),
    }
    # When: an actual CLI process, not a sender mock.
    completed = subprocess.run(
        [sys.executable, str(_SCRIPTS / "procure_cli.py"), "review", "--file", str(file) if filename else ""],
        env=environment, cwd=root, capture_output=True, text=True, timeout=30, check=False,
    )
    # Then: malformed filename is a real input error, never a misleading success.
    if filename:
        assert completed.returncode == 0, completed.stderr
        records = list(stub.iterdir())
        assert len(records) == 1
        assert "REVIEW-DM-SENT" in completed.stdout
        content = json.loads(records[0].read_text())["content"]
        assert file.name in content.splitlines()[2]
        assert "https://" not in content
    else:
        assert completed.returncode == 2
        assert "REVIEW-DM-SENT" not in completed.stdout
        assert list(stub.iterdir()) == []
