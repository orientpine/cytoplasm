"""Hermes review transport characterization shared by doctype and proposal."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol, assert_never
from unittest.mock import Mock

import pytest

from skills.doctype.scripts import doctype_review
from skills.proposal.scripts import proposal_dm


class ReviewSender(Protocol):
    DeliveryError: type[RuntimeError]

    def send_review(self, target: str, message: str, file: Path | None = None) -> None: ...

    def _chunks(self, message: str) -> tuple[str, ...]: ...


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
@pytest.mark.parametrize("body, chunks", [
    ("", ("",)),
    ("가" * 1800, ("가" * 1800,)),
    ("가" * 1801, ("가" * 1800, "가")),
    ("가" * 1799 + "\n\n끝", ("가" * 1799, "끝")),
    ("\n" + "가" * 1800, ("\n" + "가" * 1799, "가")),
])
def test_chunks_preserve_bytes_when_boundaries_differ(sender: ReviewSender, body: str, chunks: tuple[str, ...]) -> None:
    # Given a fixed body; when split; then exact UTF-8 chunks match the old algorithm.
    actual = sender._chunks(body)
    assert tuple(chunk.encode() for chunk in actual) == tuple(chunk.encode() for chunk in chunks)


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
def test_hermes_argv_preserves_bytes_when_review_is_chunked(sender: ReviewSender, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a multiline review and a successful subprocess boundary.
    body = "가" * 1799 + "\n\n끝"
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("DOCTYPE_DM_HERMES_BIN", "doctype-hermes")
    # When
    sender.send_review("discord:111", body)
    # Then: transport arguments, subprocess options and every chunk stay byte-identical.
    binary = "doctype-hermes" if sender is doctype_review else "hermes"
    assert [call.args for call in run.call_args_list] == [
        ((binary, "send", "--to", "discord:111", chunk),) for chunk in ("가" * 1799, "끝")
    ]
    assert all(call.kwargs == {
        "cwd": Path.home(),
        "env": {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"},
        "capture_output": True, "text": True, "timeout": 60, "check": False,
    } for call in run.call_args_list)


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
@pytest.mark.parametrize("failure", [7, OSError("unavailable"), subprocess.TimeoutExpired("hermes", 60)])
def test_delivery_error_stops_chunks_when_transport_fails(
    sender: ReviewSender, failure: int | OSError | subprocess.TimeoutExpired, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a transport that fails on the first chunk.
    match failure:
        case int():
            run = Mock(return_value=subprocess.CompletedProcess([], failure))
            expected = "owner DM rc=7"
        case OSError() | subprocess.TimeoutExpired():
            run = Mock(side_effect=failure)
            expected = type(failure).__name__
        case _:
            assert_never(failure)
    monkeypatch.setattr(subprocess, "run", run)
    # When
    with pytest.raises(sender.DeliveryError) as caught:
        sender.send_review("discord:111", "x" * 3601)
    # Then
    assert str(caught.value) == expected
    assert run.call_count == 1


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
def test_transport_is_unused_when_target_is_disabled(sender: ReviewSender, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    # When
    sender.send_review("", "review")
    # Then
    run.assert_not_called()


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
@pytest.mark.parametrize("available", [True, False])
def test_legacy_bytes_are_sent_when_envelope_is_unavailable(
    sender: ReviewSender, available: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an old runtime or a well-defined renderer refusal.
    from automation.interop import owner_message as om

    body = "review\n" + "가" * 1801
    if available:
        monkeypatch.setattr(om, "render", Mock(side_effect=om.OwnerMessageError(detail="message.fact")))
    else:
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(subprocess, "run", run)
    # When
    sender.send_review("discord:111", body, Path("draft.md"))
    # Then: fallback preserves the input body, even across multiple chunks.
    assert [call.args[0][-1].encode() for call in run.call_args_list] == [
        chunk.encode() for chunk in ("review", "가" * 1800, "가")
    ]


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
@pytest.mark.parametrize("file", [Path("documents/draft with spaces.md"), Path("")])
def test_document_ref_is_truthful_when_only_a_path_is_known(
    sender: ReviewSender, file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a real renderer, no channel coordinates and potentially an empty filename.
    from automation.interop import owner_message as om

    renderer = Mock(wraps=om.render)
    monkeypatch.setattr(om, "render", renderer)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(subprocess, "run", run)
    # When
    sender.send_review("discord:111", "review", file)
    # Then
    envelope = renderer.call_args.args[0]
    assert envelope.subject_key == str(file)
    assert envelope.location == om.Ref(scope="resource", search=("문서 검색", file.name))
    assert renderer.call_args.kwargs["destination"] == om.Ref(scope="none")
    assert run.call_args.args[0][-1] == om.render(envelope, destination=om.Ref(scope="none"))


@pytest.mark.parametrize("sender", [doctype_review, proposal_dm])
def test_rendered_bytes_use_original_chunks_when_envelope_is_long(
    sender: ReviewSender, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a renderer boundary returning a deliberately multi-chunk body.
    from automation.interop import owner_message as om

    body = "가" * 1799 + "\n\n끝"
    monkeypatch.setattr(om, "render", Mock(return_value=body))
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("DOCTYPE_DM_HERMES_BIN", "doctype-hermes")
    # When
    sender.send_review("discord:111", "legacy", Path("draft.md"))
    # Then: migration may change content, never argv shape or chunk bytes.
    binary = "doctype-hermes" if sender is doctype_review else "hermes"
    assert [call.args[0] for call in run.call_args_list] == [
        (binary, "send", "--to", "discord:111", chunk) for chunk in ("가" * 1799, "끝")
    ]
