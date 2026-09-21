"""ON-1..ON-3: 첨부가 있는 통지도 파사드 안에서만 나간다.

구매 검토 요청은 초안 파일을 함께 올린다. 그 전송이 파사드 밖에 있으면 목적지 규칙
(`owner_notice_channel_id` 없으면 소유자 DM)이 두 벌이 되고, 둘째 사본이 드리프트한다.
그래서 multipart 인코딩만 파사드 안에 두고, 목적지·실패 마커 계약은 그대로 둔다.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from automation import owner_notice


@pytest.fixture(autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-token")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "notice-1")


@pytest.fixture
def draft(tmp_path: Path) -> Path:
    file = tmp_path / "draft.hwpx"
    _ = file.write_bytes(b"\x00binary-draft")
    return file


def test_attachments_reach_the_file_transport_when_a_notice_carries_one(
    draft: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given both facade transports stubbed; when a notice carries an attachment;
    plain: list[tuple[str, str]] = []
    files: list[tuple[str, str, tuple[Path, ...]]] = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda _t, channel, body: plain.append((channel, body)))
    monkeypatch.setattr(
        owner_notice, "send_notice_files",
        lambda _t, channel, body, attachments: files.append((channel, body, tuple(attachments))),
        raising=False,
    )

    assert owner_notice.notify_owner("본문", attachments=(draft,)) is True
    # Then only the file transport runs, with the resolved destination and body.
    assert files == [("notice-1", "본문", (draft,))]
    assert plain == []


def test_body_only_notice_keeps_the_shared_transport_when_no_attachment_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given no attachment; when a notice is delivered; then the existing seam is unchanged.
    plain: list[tuple[str, str]] = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda _t, channel, body: plain.append((channel, body)))
    monkeypatch.setattr(
        owner_notice, "send_notice_files",
        Mock(side_effect=AssertionError("첨부가 없으면 multipart 를 쓰지 않는다")), raising=False,
    )

    assert owner_notice.notify_owner("본문") is True

    assert plain == [("notice-1", "본문")]


def test_owner_dm_receives_the_attachment_when_no_notice_channel_is_configured(
    draft: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given no configured notice channel; when a notice carries an attachment;
    files: list[str] = []
    monkeypatch.delenv("OWNER_NOTICE_CHANNEL_ID")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda _home=None: "")
    monkeypatch.setattr(owner_notice, "_config_owner_id", lambda: "owner-1")
    monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda _token, _owner: "owner-dm")
    monkeypatch.setattr(
        owner_notice, "send_notice_files",
        lambda _t, channel, _body, _attachments: files.append(channel), raising=False,
    )

    assert owner_notice.notify_owner("본문", attachments=(draft,)) is True
    # Then the same destination rule applies — no second resolver.
    assert files == ["owner-dm"]


def test_failure_marker_is_unchanged_when_the_attachment_transport_fails(
    draft: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given an attachment transport failure; when a notice is delivered;
    monkeypatch.setattr(
        owner_notice, "send_notice_files", Mock(side_effect=OSError("offline")), raising=False,
    )

    assert owner_notice.notify_owner("본문", attachments=(draft,)) is False
    # Then the caller sees no exception and the marker name is untouched.
    assert capsys.readouterr().err == "[owner-notice] NOTIFY-FAILED: OSError\n"


def test_unconfigured_marker_is_unchanged_when_credentials_are_missing(
    draft: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given no credentials; when an attachment notice is attempted;
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "")
    monkeypatch.setattr(
        owner_notice, "send_notice_files",
        Mock(side_effect=AssertionError("자격 없이 전송하지 않는다")), raising=False,
    )

    assert owner_notice.notify_owner("본문", attachments=(draft,)) is False

    assert "NOTIFY-UNCONFIGURED:" in capsys.readouterr().err


def test_multipart_carries_payload_and_file_bytes_when_encoded(draft: Path) -> None:
    # Given one draft; when the facade encodes a multipart body;
    boundary, body = owner_notice._multipart("본문", (draft,))
    # Then the payload names the file and the raw bytes follow it verbatim.
    head, _, tail = body.partition(f"\r\n--{boundary}\r\n".encode("utf-8"))
    payload = json.loads(head.split(b"\r\n\r\n", 1)[1].decode("utf-8"))
    assert payload == {"content": "본문", "attachments": [{"id": 0, "filename": "draft.hwpx"}]}
    assert b'name="files[0]"; filename="draft.hwpx"' in tail
    assert tail.endswith(b"\x00binary-draft" + f"\r\n--{boundary}--\r\n".encode("utf-8"))


def test_remaining_chunks_follow_the_attachment_when_the_body_is_long(
    draft: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a body longer than one Discord message; when sent with an attachment;
    body = "가" * 2001
    posted: list[tuple[str, str, tuple[Path, ...]]] = []
    plain: list[tuple[str, str]] = []
    monkeypatch.setattr(
        owner_notice, "_post_multipart",
        lambda _t, channel, head, files: posted.append((channel, head, tuple(files))), raising=False,
    )
    monkeypatch.setattr(owner_notice, "send_notice", lambda _t, channel, rest: plain.append((channel, rest)))

    owner_notice.send_notice_files("unit-token", "notice-1", body, (draft,))
    # Then the attachment rides the first chunk and the remainder keeps its order.
    assert posted == [("notice-1", "가" * 2000, (draft,))]
    assert plain == [("notice-1", "가")]
