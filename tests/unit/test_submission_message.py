"""Submission wire compatibility; canonical prose is machine-consumed by the parser."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from typing import Final

import pytest

from automation.interop import approval_surface, owner_message
from automation.managed_skills import submission_message as wire
from automation.managed_skills.submission_errors import SubmissionArtifactError


# 전체 본문은 exact-match 파서의 기계 입력이다. 현재 렌더러로 기대값을 만들지 않는다.
_STORED_V2: Final = (
    '[personal-skill-submission-v2] {"action_hash":"sha256:'
    '212f1643f7016e27bdee9562ccb7241ca4fefdfec0723f7c8387d925a817f928",'
    '"group_id":"group-a","manifest_filename":"managed-x.json",'
    '"manifest_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    '"nonce":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","skill":"managed-x",'
    '"skill_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
    '"source_commit":"dddddddddddddddddddddddddddddddddddddddd","submitter":"member-a",'
    '"tarball_filename":"managed-x.tar.gz",'
    '"tarball_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}\n'
    '대상: managed-x (sha256:212f1643f7016e27bdee9562ccb7241ca4fefdfec0723f7c8387d925a817f928)\n'
    '사실: 그룹 group-a · 제출자 member-a (승인 요청; 만료: 기한 없음)\n'
    '위치: 이 메시지\n'
    '인계: 소유자: 위 위치 · 반응 ✅ 실행 / ⛔ 취소; 다음: 관리자 명시 발행 시 재검증; 자동 발행 없음\n'
    '되돌리기: 해당 없음; 취소 시: 제출 검토 취소; 발행하지 않음'
)


@pytest.fixture
def envelope() -> wire.SubmissionEnvelope:
    return wire.SubmissionEnvelope(
        "sha256:212f1643f7016e27bdee9562ccb7241ca4fefdfec0723f7c8387d925a817f928",
        "group-a", "managed-x.json", "a" * 64, "b" * 32, "managed-x",
        "c" * 64, "d" * 40, "member-a", "managed-x.tar.gz", "e" * 64,
    )


def stored_v1(envelope: wire.SubmissionEnvelope) -> str:
    """Base 2886f0a5c's exact canonical wire bytes, independent of the live renderer."""
    payload = json.dumps(asdict(envelope), separators=(",", ":"), sort_keys=True)
    return f"[personal-skill-submission-v1] {payload}\n이 메시지에 ✅ 실행 / ⛔ 취소"


def test_v1_parses_when_stored_before_migration(envelope: wire.SubmissionEnvelope) -> None:
    # Given: a stored, synthetic v1 card from the base renderer.
    content = stored_v1(envelope)
    # When: the wire parser verifies it.
    parsed = wire.parse_submission_message(content)
    # Then: every structured field survives canonical verification.
    assert parsed == envelope


def test_hash_when_inputs_are_identical_stays_pinned(envelope: wire.SubmissionEnvelope) -> None:
    # Given: fixed structured inputs captured before migration.
    # When: the semantic hash is computed.
    digest = wire._semantic_hash(envelope)
    # Then: the original hash preimage is unchanged.
    assert digest == envelope.action_hash


@pytest.mark.parametrize("change", ["prefix", "truncated", "attachment", "body", "json"])
def test_v1_refuses_when_wire_is_altered(
    envelope: wire.SubmissionEnvelope, change: str,
) -> None:
    # Given: alterations to each canonical boundary, including the human body.
    content = stored_v1(envelope)
    mutations = {
        "prefix": content.replace("submission-v1", "submission-v99"),
        "truncated": content.splitlines()[0],
        "attachment": content.replace('"tarball_filename":"managed-x.tar.gz",', ""),
        "body": content[:-1] + "X",
        "json": content.replace('{"action_hash"', '{ "action_hash"'),
    }
    # When / Then: malformed or noncanonical input is refused.
    with pytest.raises(SubmissionArtifactError):
        _ = wire.parse_submission_message(mutations[change])


def test_render_refuses_when_wire_exceeds_limit(envelope: wire.SubmissionEnvelope) -> None:
    # Given: a structured submission too large even before the human body.
    oversized = replace(envelope, submitter="x" * 1900)
    # When / Then: no truncated approval can be returned for posting.
    with pytest.raises(SubmissionArtifactError, match="1900"):
        _ = wire.render_submission_message(oversized)


def test_new_card_when_capability_exists_uses_v3(envelope: wire.SubmissionEnvelope) -> None:
    # Given: a valid submission and the available envelope capability.
    # When: a new card is rendered.
    content = wire.render_submission_message(envelope)
    # Then: the new wire prefix dispatches to exact verification of five human fields.
    assert content.splitlines()[-1].startswith("-# [personal-skill-submission-v3] ")
    assert len(content.splitlines()) == 8
    assert len(content) <= 1900
    assert wire.parse_submission_message(content) == envelope


def test_v2_renderer_when_replayed_matches_complete_stored_wire(
    envelope: wire.SubmissionEnvelope,
) -> None:
    # Given: the fixed record represented by the independent complete v2 wire fixture.
    # When: the public render root replays that record.
    content = wire._render_v2(envelope)
    assert content is not None
    # Then: every UTF-8 byte, including shared labels, stays compatible with stored cards.
    assert content.encode("utf-8") == _STORED_V2.encode("utf-8")


def test_v2_parser_when_complete_stored_wire_is_verified_preserves_record(
    envelope: wire.SubmissionEnvelope,
) -> None:
    # Given: complete stored v2 bytes, not output from any current renderer.
    # When: the real parser verifies the stored wire, including its exact-body comparison.
    parsed = wire.parse_submission_message(_STORED_V2)
    # Then: the original record survives canonical verification without a round trip setup.
    assert parsed == envelope


def test_new_card_when_rendered_carries_existing_values_and_self_action(
    envelope: wire.SubmissionEnvelope, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a recording wrapper around the real renderer (not a canned result).
    observed: list[tuple[owner_message.OwnerMessage, owner_message.Ref]] = []
    original = owner_message.render

    def capture(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        observed.append((message, destination))
        return original(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", capture)
    # When: the submission render root is used.
    _ = wire.render_submission_message(envelope)
    # Then: existing identifiers and approval semantics reach the common renderer.
    assert len(observed) == 1
    message, destination = observed[0]
    assert message.subject == envelope.skill
    assert message.subject_key == envelope.action_hash
    assert envelope.group_id in message.fact and envelope.submitter in message.fact
    assert message.location.scope == destination.scope == "self"
    assert message.owner.verb == "react"
    assert message.owner.target == message.location
    assert message.owner.argument is not None
    assert "✅" in message.owner.argument and "⛔" in message.owner.argument
    assert isinstance(message.detail, owner_message.Approval)
    assert message.detail.expires_at is None


def test_v1_when_shared_instruction_changes_still_verifies(
    envelope: wire.SubmissionEnvelope, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the unrelated shared instruction has a future revision.
    def future_instruction(
        kind: approval_surface.ApprovalKind, surface: approval_surface.ApprovalSurface,
    ) -> str:
        del kind, surface
        return "future"

    monkeypatch.setattr(approval_surface, "reaction_instruction", future_instruction)
    # Patch the old imported alias too, if it exists on the base implementation.
    if hasattr(wire, "reaction_instruction"):
        monkeypatch.setattr(wire, "reaction_instruction", future_instruction)
    # When: a stored v1 card is parsed.
    parsed = wire.parse_submission_message(stored_v1(envelope))
    # Then: its canonical instruction is frozen, not the shared current phrase.
    assert parsed == envelope


@pytest.mark.parametrize("failure", ["import", "render"])
def test_new_card_when_capability_fails_falls_back_byte_exactly(
    envelope: wire.SubmissionEnvelope, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    # Given: the optional import or rendering capability is unavailable.
    def refused(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        del message, destination
        raise owner_message.OwnerMessageError(detail="test")

    if failure == "import":
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    else:
        monkeypatch.setattr(owner_message, "render", refused)
    # When: a new card is selected.
    content = wire.render_submission_message(envelope)
    # Then: it is exactly the stored v1 contract, not v2 with a legacy body.
    assert content == stored_v1(envelope)
    assert wire.parse_submission_message(content) == envelope


@pytest.mark.parametrize("change", ["prefix", "truncated", "attachment", "body", "json"])
def test_new_card_when_altered_is_refused(
    envelope: wire.SubmissionEnvelope, change: str,
) -> None:
    # Given: a new-format card with one invalid boundary.
    content = wire.render_submission_message(envelope)
    mutations = {
        "prefix": content.replace("submission-v3", "submission-v99"),
        "truncated": content.splitlines()[0],
        "attachment": content.replace('"tarball_filename":"managed-x.tar.gz",', ""),
        "body": content[:-1] + "X",
        "json": content.replace('{"action_hash"', '{ "action_hash"'),
    }
    # When / Then: the parser refuses it instead of relaxing canonical exactness.
    with pytest.raises(SubmissionArtifactError):
        _ = wire.parse_submission_message(mutations[change])


def test_new_card_when_at_limit_preserves_every_byte(envelope: wire.SubmissionEnvelope) -> None:
    # Given: a nonce padded to make the complete selected card exactly 1900 characters.
    base = wire.render_submission_message(envelope)
    at_limit = replace(envelope, nonce=envelope.nonce + "a" * (1900 - len(base)))
    # When: the complete card is rendered.
    content = wire.render_submission_message(at_limit)
    # Then: the boundary is accepted exactly, not truncated.
    assert len(content) == 1900
    assert wire.parse_submission_message(content) == at_limit


def test_new_card_when_one_over_limit_refuses_instead_of_downgrading(
    envelope: wire.SubmissionEnvelope,
) -> None:
    # Given: v2 exceeds 1900 while the smaller v1 would still fit.
    base = wire.render_submission_message(envelope)
    over_limit = replace(envelope, nonce=envelope.nonce + "a" * (1901 - len(base)))
    # When / Then: exceeding the selected format's cap is a posting refusal, not fallback.
    with pytest.raises(SubmissionArtifactError, match="1900"):
        _ = wire.render_submission_message(over_limit)


def test_v1_renderer_when_replayed_matches_base_bytes(envelope: wire.SubmissionEnvelope) -> None:
    # Given: canonical wire bytes consumed by the base parser, not a prose snapshot.
    expected = stored_v1(envelope)
    # When: the frozen renderer replays this record.
    content = wire._render_v1(envelope)
    # Then: byte-exact legacy comparison still accepts it.
    assert content.encode("utf-8") == expected.encode("utf-8")


def test_v2_parser_when_capability_disappears_preserves_stored_version(
    envelope: wire.SubmissionEnvelope, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a previously posted v2 card, followed by a missing optional capability.
    content = wire.render_submission_message(envelope)
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    # When / Then: the stored v2 remains readable, without downgrading its wire grammar.
    assert wire.parse_submission_message(content) == envelope
    downgraded = content.split("\n", 1)[0] + "\n" + stored_v1(envelope).split("\n", 1)[1]
    with pytest.raises(SubmissionArtifactError):
        _ = wire.parse_submission_message(downgraded)
