from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

from automation.interop import injection_adapter  # noqa: E402
import triage_approval  # noqa: E402
import triage_binding  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402


def test_render_approval_message_when_draft_is_unbound_does_not_import_approval_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the mounted skill has no AUTOPHAGY_REPO_ROOT checkout to import.
    draft = {
        "body": "검토 부탁드립니다.",
        "category": "important",
        "flags": ["reply_needed"],
        "id": "draft123",
        "kind": "reply",
        "mail_subject": "장비 확인",
        "sender_masked": "sha256:sender",
        "sensitive": False,
        "sha256": "sha256:draft",
        "subject": "Re: 장비 확인",
        "tags": [],
        "uid_opaque": "sha256:uid",
    }

    def reject_repo_import(_name: str) -> ModuleType:
        raise AssertionError("draft rendering must not import approval policy")

    monkeypatch.setattr(triage_binding, "_repo_module", reject_repo_import)

    # When: draft-only processing renders the pending approval message.
    message = triage_core.render_approvals_message(draft)

    # Then: it remains surface-neutral without importing policy just to render a draft.
    assert "반응(기본)" not in message


def test_resolve_reaction_when_binding_is_persisted_does_not_import_approval_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a message whose fact-checked, concrete surface was persisted at post time.
    draft = {
        "channel_id": "100000000000000001",
        "kind": "reply",
        "message_id": "message-1",
        "policy_version": 1,
        "sha256": "sha256:draft",
        "surface": "skill-approvals",
    }

    def reject_repo_import(_name: str) -> ModuleType:
        raise AssertionError("stored binding replay must not import approval policy")

    def approval_message(method: str, path: str, payload: dict | None = None) -> dict[str, str]:
        del payload
        assert (method, path) == ("GET", "/channels/100000000000000001/messages/message-1")
        return {"content": "draft sha256:draft"}

    def owner_approval(
        _channel_id: str,
        _message_id: str,
        emoji: str,
    ) -> list[dict[str, str | bool]]:
        if emoji == triage_confirm.APPROVE_EMOJI:
            return [{"id": "owner-1", "bot": False}]
        return []

    monkeypatch.setattr(triage_binding, "_repo_module", reject_repo_import)
    monkeypatch.setattr(triage_confirm, "_api", approval_message)
    monkeypatch.setattr(triage_confirm, "_reaction_users", owner_approval)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "owner-1")

    # When: the watcher reads an owner reaction from its stored approval message.
    action = triage_confirm.resolve_reaction(draft)

    # Then: it uses that exact stored channel without a policy import or fallback.
    assert action == triage_confirm.APPROVE_EMOJI


def _persisted_injection_draft() -> dict[str, str | int]:
    return {
        "channel_id": "100000000000000001",
        "id": "draft123",
        "kind": "reply",
        "message_id": "message-1",
        "policy_version": 1,
        "sha256": "sha256:draft",
        "surface": "skill-approvals",
    }


def test_sign_injection_when_binding_is_persisted_does_not_resolve_a_new_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: an E2E-only event request for a draft whose post path stored its binding.
    draft = _persisted_injection_draft()
    output = tmp_path / "event.json"

    def reject_binding(_draft: dict) -> None:
        raise AssertionError("signed injection must not resolve a new binding")

    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv("INTEROP_E2E_SECRET", "test-secret")
    monkeypatch.setattr(triage_approval, "stored_binding", reject_binding)
    monkeypatch.setattr(triage_confirm, "_adapter", lambda: injection_adapter)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "owner-1")

    # When: the E2E signer creates its synthetic owner event.
    triage_confirm.sign_injection(draft, output, None, None, False)

    # Then: the signed event retains the persisted channel without re-resolution.
    assert json.loads(output.read_text(encoding="utf-8"))["event"]["channel_id"] == draft["channel_id"]


def test_confirm_injection_when_binding_is_persisted_does_not_resolve_a_new_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: a valid E2E event whose channel is already stamped on its draft.
    draft = _persisted_injection_draft()
    event = injection_adapter.InboundEvent(
        event_id="event-1",
        user_id="owner-1",
        channel_id=str(draft["channel_id"]),
        text=triage_confirm.confirm_text(draft),
    )
    secret = b"test-secret"
    injection_path = tmp_path / "event.json"
    injection_path.write_text(
        json.dumps({
            "event": {
                "event_id": event.event_id,
                "user_id": event.user_id,
                "channel_id": event.channel_id,
                "text": event.text,
            },
            "signature": injection_adapter.sign_event(event, secret),
        }),
        encoding="utf-8",
    )

    def reject_binding(_draft: dict) -> None:
        raise AssertionError("injected confirmation must not resolve a new binding")

    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv("INTEROP_E2E_SECRET", secret.decode("utf-8"))
    monkeypatch.setattr(triage_approval, "stored_binding", reject_binding)
    monkeypatch.setattr(triage_confirm, "_adapter", lambda: injection_adapter)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "owner-1")

    # When: the E2E confirmation validates the signed owner event.
    approval_ref = triage_confirm.confirm_via_injection(draft, injection_path)

    # Then: it accepts the event against the persisted channel without resolution.
    assert approval_ref == "injected:event-1"
