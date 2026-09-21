"""ON-1..ON-3: doctype·proposal 검토 안내는 통지 파사드로만 나간다.

이관 전 특성화(characterization): 렌더된 본문 바이트는 그대로다. 달라지는 것은
목적지 해석(hermes CLI 대상 → `owner_notice_channel_id` 없으면 소유자 DM)과
청크 소유자(모듈 사본 → `automation.interop.chunker`)뿐이다. 그래서 여기서
고정하는 것은 "파사드로 정확히 한 번, 같은 바이트"이다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final, Protocol
from unittest.mock import Mock

import pytest

from automation import owner_notice
from skills.doctype.scripts import doctype_review
from skills.proposal.scripts import proposal_dm


class ReviewSender(Protocol):
    DeliveryError: type[RuntimeError]

    def send_review(self, target: str, message: str, file: Path | None = None) -> None: ...


_DOCUMENT: Final = Path("documents/draft with spaces.md")
# 2ba312691 이전 발신자에서 실측한 바이트. 살아있는 빌더에서 유도하지 않는다.
_RENDERED: Final = {
    "doctype_review": (
        "대상: 서류 초안 (documents/draft with spaces.md)\n"
        "사실: 검토 요청 (실행 완료)\n"
        "위치: 링크 없음 (주소 없음); 검색: 문서 검색 / draft with spaces.md\n"
        "인계: 소유자: 위 위치 · 열기 검토·제출은 직접; 다음: 추가 실행 없음\n"
        "되돌리기: 해당 없음"
    ),
    "proposal_dm": (
        "대상: 제안서 (documents/draft with spaces.md)\n"
        "사실: 최종 검토 완료 (실행 완료)\n"
        "위치: 링크 없음 (주소 없음); 검색: 문서 검색 / draft with spaces.md\n"
        "인계: 소유자: 위 위치 · 열기; 다음: 추가 실행 없음\n"
        "되돌리기: 해당 없음"
    ),
}


def _name(sender: ReviewSender) -> str:
    return "doctype_review" if sender is doctype_review else "proposal_dm"


@pytest.fixture
def delivered(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """파사드의 마지막 전송 계층만 스텁한다 — 목적지 해석은 실제 코드가 한다."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-token")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "notice-1")
    monkeypatch.setattr(
        owner_notice, "send_notice", lambda _token, channel, body: sent.append((channel, body)),
    )
    monkeypatch.setattr(
        subprocess, "run", Mock(side_effect=AssertionError("검토 안내는 파사드 밖으로 나가지 않는다")),
    )
    return sent


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
@pytest.mark.parametrize("body", [
    pytest.param("", id="empty"),
    pytest.param("가" * 1801, id="past-legacy-chunk-limit"),
    pytest.param("가" * 1799 + "\n\n끝", id="newline-boundary"),
])
def test_facade_receives_every_byte_once_when_review_is_sent(
    sender: ReviewSender, body: str, delivered: list[tuple[str, str]],
) -> None:
    # Given a body the old transport would have split; when delivered;
    sender.send_review("discord:111", body)
    # Then the facade is called exactly once with the identical bytes (it owns chunking).
    assert delivered == [("notice-1", body)]
    assert [chunk.encode() for _channel, chunk in delivered] == [body.encode()]


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
def test_owner_dm_receives_review_when_notice_channel_is_unset(
    sender: ReviewSender, delivered: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given no configured notice channel; when a review is delivered;
    monkeypatch.delenv("OWNER_NOTICE_CHANNEL_ID")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda _home=None: "")
    monkeypatch.setattr(owner_notice, "_config_owner_id", lambda: "owner-1")
    monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda _token, _owner: "owner-dm")

    sender.send_review("discord:111", "review")
    # Then the facade's own DM fallback decides the destination.
    assert delivered == [("owner-dm", "review")]


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
def test_delivery_error_when_facade_reports_a_failed_notice(
    sender: ReviewSender, delivered: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a transport failure inside the never-raising facade;
    monkeypatch.setattr(owner_notice, "send_notice", Mock(side_effect=OSError("unavailable")))
    # When; Then the skill keeps its own refusal contract for the CLI.
    with pytest.raises(sender.DeliveryError):
        sender.send_review("discord:111", "review")
    assert delivered == []


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
def test_transport_is_unused_when_target_is_disabled(
    sender: ReviewSender, delivered: list[tuple[str, str]],
) -> None:
    # Given the skill's disable switch; when a review is requested; then nothing is sent.
    sender.send_review("", "review")

    assert delivered == []


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
def test_rendered_bytes_are_unchanged_when_a_document_is_known(
    sender: ReviewSender, delivered: list[tuple[str, str]],
) -> None:
    # Given a document locator; when the review is delivered;
    sender.send_review("discord:111", "legacy", _DOCUMENT)
    # Then the envelope bytes are exactly the pre-migration ones.
    assert delivered == [("notice-1", _RENDERED[_name(sender)])]


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
@pytest.mark.parametrize("available", [True, False])
def test_legacy_bytes_are_sent_when_envelope_is_unavailable(
    sender: ReviewSender, available: bool, delivered: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an old runtime or a well-defined renderer refusal.
    from automation.interop import owner_message as om

    body = "review\n" + "가" * 1801
    if available:
        monkeypatch.setattr(om, "render", Mock(side_effect=om.OwnerMessageError(detail="message.fact")))
    else:
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    # When
    sender.send_review("discord:111", body, _DOCUMENT)
    # Then the capability fallback still delivers the caller's own bytes, uncut.
    assert delivered == [("notice-1", body)]


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
@pytest.mark.parametrize("file", [_DOCUMENT, Path("")])
def test_document_ref_is_truthful_when_only_a_path_is_known(
    sender: ReviewSender, file: Path, delivered: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a real renderer, no channel coordinates and potentially an empty filename.
    from automation.interop import owner_message as om

    renderer = Mock(wraps=om.render)
    monkeypatch.setattr(om, "render", renderer)
    # When
    sender.send_review("discord:111", "review", file)
    # Then the sender keeps rendering its own body; the facade must not re-render it.
    envelope = renderer.call_args.args[0]
    assert envelope.subject_key == str(file)
    assert envelope.location == om.Ref(scope="resource", search=("문서 검색", file.name))
    assert renderer.call_args.kwargs["destination"] == om.Ref(scope="none")
    assert delivered == [("notice-1", om.render(envelope, destination=om.Ref(scope="none")))]
