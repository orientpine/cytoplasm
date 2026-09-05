from __future__ import annotations

from pathlib import Path

import pytest

from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.classify_veto import post_llm_veto, pre_llm_veto
from automation.rag_ingest.sensitivity import SensitivityRule, load_rules


def _rules() -> tuple[SensitivityRule, ...]:
    return load_rules(
        Path(__file__).parents[2] / "configs" / "sensitivity-rules.yaml"
    )


def test_pre_llm_veto_closes_sensitive_text_without_an_llm() -> None:
    # Given a real sensitivity rule hit
    text = "새 발명 아이디어의 공개 전 검토 자료"

    # When the pre-LLM safety layer classifies it
    verdict = pre_llm_veto(text, source_kind="memory", rules=_rules())

    # Then the route is closed before any LLM path is needed.
    assert verdict == EntryVerdict(
        source_kind="memory",
        entry_text=text,
        route="UNCERTAIN",
        evidence="",
        reason="sensitivity",
        veto="sensitivity",
        llm_called=False,
    )


def test_pre_llm_veto_prefers_sensitivity_over_later_vetoes() -> None:
    # Given text that matches both V1 sensitivity and V2 credential
    text = "특허 검토 api_key: placeholder"

    # When the ordered pre-LLM veto table runs
    verdict = pre_llm_veto(text, source_kind="memory", rules=_rules())

    # Then the first hit wins.
    assert verdict is not None
    assert verdict.veto == "sensitivity"


@pytest.mark.parametrize(
    "text",
    [
        "api_key: placeholder",
        "authorization: Bearer placeholder",
        "secret=placeholder",
        "passphrase: placeholder",
        "-----BEGIN PRIVATE KEY-----",
        "A" * 40,
    ],
)
def test_pre_llm_veto_rejects_each_credential_shape(text: str) -> None:
    # Given a vendor-agnostic credential-shaped entry / When it is classified
    verdict = pre_llm_veto(text, source_kind="memory", rules=_rules())

    # Then it is closed without sending the text to an LLM.
    assert verdict is not None
    assert verdict.route == "UNCERTAIN"
    assert verdict.veto == "credential"
    assert verdict.llm_called is False


def test_classify_veto_source_contains_no_vendor_token_prefixes() -> None:
    # Given the production module source / When forbidden literals are reconstructed
    source = (
        Path(__file__).parents[2]
        / "automation"
        / "memory_curator"
        / "classify_veto.py"
    ).read_text(encoding="utf-8")
    forbidden_prefixes = ("s" + "k-", "g" + "hp_", "B" + "ot ")

    # Then no real vendor token prefix can trip the repository secret scan.
    assert all(prefix not in source for prefix in forbidden_prefixes)


@pytest.mark.parametrize(
    "text",
    [
        "내 소속은 KIMM이다",
        "답변은 한국어로 작성한다",
        "확인 없이 실행하지 않는다",
        "자료를 recall 검색한다",
    ],
)
def test_pre_llm_veto_keeps_each_native_cue_group(text: str) -> None:
    # Given an identity, style, safety, or routing rule / When it is classified
    verdict = pre_llm_veto(text, source_kind="memory", rules=_rules())

    # Then the deterministic native rule overrides the short-text fallback.
    assert verdict is not None
    assert verdict.route == "KEEP_NATIVE"
    assert verdict.veto == "keep_native_rule"


def test_pre_llm_veto_keeps_marker_entries() -> None:
    # Given a curator marker / When it is classified
    text = "<!-- mc-marker-v1 promoted -->"
    verdict = pre_llm_veto(text, source_kind="memory", rules=_rules())

    # Then V4 wins before the short-text fallback.
    assert verdict is not None
    assert verdict.route == "KEEP_NATIVE"
    assert verdict.veto == "marker"


def test_pre_llm_veto_uses_collapsed_length_boundary() -> None:
    # Given plain entries at the exact collapsed-length boundary
    short_text = "가" * 59
    eligible_text = "가" * 60

    # When each entry is classified
    short_verdict = pre_llm_veto(
        short_text,
        source_kind="memory",
        rules=_rules(),
    )
    eligible_verdict = pre_llm_veto(
        eligible_text,
        source_kind="memory",
        rules=_rules(),
    )

    # Then only the 59-character entry is vetoed.
    assert short_verdict is not None
    assert short_verdict.veto == "too_short"
    assert eligible_verdict is None


def test_pre_llm_veto_allows_plain_ops_fact() -> None:
    # Given a long operations fact with no native-rule cue
    text = (
        "모델 프록시 서비스는 127.0.0.1 포트 4000에서 작동하며 config 파일은 "
        "~/.hermes/ 아래에 있고 운영 로그는 별도 디렉터리에 기록된다"
    )

    # When the pre-LLM veto table runs / Then the entry remains LLM-eligible.
    assert pre_llm_veto(text, source_kind="memory", rules=_rules()) is None


def test_post_llm_veto_keeps_user_file_ops_reference() -> None:
    # Given an LLM-derived user-file operations route with no V6 cue
    text = "가" * 60
    verdict = EntryVerdict(
        source_kind="user",
        entry_text=text,
        route="OPS_REFERENCE",
        evidence="가" * 8,
        reason="operations reference",
        veto=None,
        llm_called=True,
    )

    # When the post-LLM veto table runs
    final = post_llm_veto(verdict)

    # Then V7 keeps the entry native while retaining that the LLM was called.
    assert final == EntryVerdict(
        source_kind="user",
        entry_text=text,
        route="KEEP_NATIVE",
        evidence="",
        reason="user_file",
        veto="user_file",
        llm_called=True,
    )


def test_post_llm_veto_leaves_memory_ops_reference_unchanged() -> None:
    # Given a valid memory-file operations route with no V6 cue
    verdict = EntryVerdict(
        source_kind="memory",
        entry_text="가" * 60,
        route="OPS_REFERENCE",
        evidence="가" * 8,
        reason="operations reference",
        veto=None,
        llm_called=True,
    )

    # When the post-LLM veto table runs / Then the original verdict is unchanged.
    assert post_llm_veto(verdict) is verdict


@pytest.mark.parametrize(
    ("text", "expected_veto"),
    [
        ("승인 없이 실행한다", "keep_native_rule"),
        ("<!-- mc-marker-v1 promoted -->", "marker"),
        ("가" * 59, "too_short"),
    ],
)
def test_post_llm_veto_overrides_each_v6_native_rule(
    text: str,
    expected_veto: str,
) -> None:
    # Given an LLM-derived TWIN route that a deterministic V6 rule rejects
    verdict = EntryVerdict(
        source_kind="memory",
        entry_text=text,
        route="TWIN",
        evidence="evidence",
        reason="durable judgment",
        veto=None,
        llm_called=True,
    )

    # When the post-LLM veto table runs
    final = post_llm_veto(verdict)

    # Then the deterministic rule forces KEEP_NATIVE.
    assert final.source_kind == verdict.source_kind
    assert final.entry_text == verdict.entry_text
    assert final.route == "KEEP_NATIVE"
    assert final.veto == expected_veto
    assert final.llm_called is True


def test_post_llm_veto_is_idempotent() -> None:
    # Given a verdict that V7 replaces
    verdict = EntryVerdict(
        source_kind="user",
        entry_text="가" * 60,
        route="OPS_REFERENCE",
        evidence="가" * 8,
        reason="operations reference",
        veto=None,
        llm_called=True,
    )

    # When the final verdict is passed through the veto table again
    first = post_llm_veto(verdict)
    second = post_llm_veto(first)

    # Then no field changes on the second application.
    assert second == first
