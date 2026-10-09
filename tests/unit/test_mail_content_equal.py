"""Content keywords must not change mail storage, disclosure or model access."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/mail/scripts"))

import mail_body_view  # noqa: E402
import mail_wrapper_classification  # noqa: E402
import triage_core  # noqa: E402
import triage_digest  # noqa: E402
import triage_gate  # noqa: E402
import triage_llm  # noqa: E402
import triage_pipeline  # noqa: E402


@pytest.mark.parametrize("text", ["일반 자료", "특허 patent 기밀 confidential"])
def test_metadata_keywords_have_the_same_action_flags(text: str) -> None:
    # Given metadata without an actionable request.
    clean = mail_wrapper_classification.classify_metadata("일반 자료", "peer@example.invalid")
    # When classifying metadata containing content keywords.
    result = mail_wrapper_classification.classify_metadata(text, "peer@example.invalid")
    # Then keywords alone neither prioritize the mail nor select a route.
    assert result == clean


@pytest.mark.parametrize("text", ["일반 자료", "특허 patent 기밀 confidential"])
def test_compose_uses_common_storage_and_visible_approval(
    text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an isolated draft store and a quoted original with the same text.
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    # When composing without publishing or sending.
    draft = triage_pipeline.compose_and_post(
        "peer@example.invalid", text, text, quote=text, post=False,
    )
    # Then all topics use the common store and the same complete approval preview.
    assert triage_gate._draft_path(draft["id"]) == tmp_path / "gate/drafts" / f"{draft['id']}.json"
    assert draft["body"] == text
    assert draft["quote"] == text
    assert "sensitive" not in draft and "tags" not in draft
    assert text in triage_core.render_approvals_message(draft)
    assert not (tmp_path / "mail/triage-drafts").exists()


@pytest.mark.parametrize("text", ["일반 자료", "특허 patent 기밀 confidential"])
def test_digest_preserves_keyword_subject_and_summary(
    text: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given deterministic model outputs without calendar or reply actions.
    monkeypatch.setattr(triage_llm, "classify", lambda **_kw: (
        triage_core.Classification("normal", False, False, False, "", "fixture"), "fixture",
    ))
    monkeypatch.setattr(triage_llm, "summarize", lambda **kw: kw["body"])
    # When building a digest item.
    owner, stored = triage_digest.build_item(
        {"uid": "u-1", "subject": text, "sender": "peer@example.invalid", "body": text}, 1,
    )
    # Then storage and owner projection preserve the text identically.
    assert owner["subject"] == stored["subject"] == text
    assert owner["summary"] == stored["summary"] == text
    assert "sensitive" not in owner and "sensitive" not in stored


@pytest.mark.parametrize("purpose", ["classify", "summarize", "draft_reply"])
@pytest.mark.parametrize("text", ["일반 자료", "특허 patent 기밀 confidential"])
def test_all_model_steps_use_account_client_without_provider_identity_ban(
    purpose: str, text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a shared client whose account can select a different provider.
    calls: list[str] = []
    output = {
        "classify": '{"category":"normal","reply_needed":false,"schedule_needed":false,"budget":false}',
        "summarize": '{"summary":"fixture"}',
        "draft_reply": '{"subject":"Re: fixture","body":"fixture"}',
    }[purpose]

    class Client:
        @staticmethod
        def from_environment(*, timeout: float) -> Client:
            return Client()

        def complete_served(self, prompt: str) -> SimpleNamespace:
            calls.append(prompt)
            return SimpleNamespace(text=output, provider="account-provider", model="account-model")

    monkeypatch.setattr(triage_llm, "_codex_module", lambda: SimpleNamespace(
        PROVIDER="account-provider", CONFIGURED_MODEL="account-config", CodexClient=Client,
    ))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(tmp_path / "calls.jsonl"))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("<<<PROMPT>>>\n{{SUBJECT}}\n{{SENDER}}\n{{BODY}}\n<<<END>>>\n")
    # When the real model step completes.
    getattr(triage_llm, purpose)(
        subject=text, sender="peer@example.invalid", body=text, uid_opaque="opaque", prompt_path=prompt,
    )
    # Then the actual client receives content without a topic-dependent refusal.
    assert len(calls) == 1 and text in calls[0]
    assert '"sensitive"' not in (tmp_path / "calls.jsonl").read_text()


@pytest.mark.parametrize("text", ["일반 자료", "특허 patent 기밀 confidential"])
def test_body_view_preserves_keyword_text(text: str) -> None:
    # Given an already-disclosed mail body.
    mail = {"subject": "fixture", "sender": "peer@example.invalid", "body": text}
    # When projecting it for Discord.
    rendered = "\n".join(mail_body_view.render(mail))
    # Then the words remain visible.
    assert text in rendered


@pytest.mark.parametrize("text", ["일반 자료", "특허 patent 기밀 confidential"])
def test_reply_card_shows_the_same_fields_for_every_topic(text: str) -> None:
    # Given a reply card in the original layout.
    draft = {
        "id": "abc123", "sha256": "fixture", "kind": "reply",
        "body": text, "subject": text, "mail_subject": text,
        "category": "important", "flags": ["reply_needed"], "sender_masked": "opaque",
    }
    # When rendering the real approval producer.
    rendered = triage_core.render_approvals_message(draft)
    # Then the subject and body stay visible, along with their approval binding.
    assert f"- 회신 제목: {text}" in rendered
    assert f"```\n{text}\n```" in rendered
    assert "abc123" in rendered and "fixture" in rendered
