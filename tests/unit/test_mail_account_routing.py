"""Explicit sender-account routing for the mail skill (repair t_0ef4df46, RTS-1 C1).

Background: triage only ever spoke to MailOn (KIMM), so a Gmail-bound mail had no
deterministic place to declare its sending account and leaked into ad-hoc local
drafts. ``mail_account_routing.select_account`` is the single source of truth:
one explicit choice, one inherited choice for replies, and a fail-closed refusal
for everything else. It NEVER falls back to a silent default — picking an account
for the owner is an external effect with the wrong blast radius.

Pure logic: no I/O, no env, no subprocess.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import mail_account_routing  # noqa: E402
from mail_account_routing import AccountSelectionError, select_account  # noqa: E402


class TestExplicitAccount:
    def test_explicit_gmail_is_selected(self) -> None:
        # Given an owner who names gmail / When selecting / Then gmail is used.
        assert select_account("gmail") == "gmail"

    def test_explicit_kimm_is_selected(self) -> None:
        # KIMM regression: the pre-existing MailOn account must stay selectable
        # exactly as before this feature landed.
        assert select_account("kimm") == "kimm"

    def test_explicit_kimm_survives_alongside_a_gmail_reply_thread(self) -> None:
        # KIMM regression: an explicit choice outranks thread inheritance, so
        # the owner can still force KIMM out of a gmail thread.
        assert select_account("kimm", reply_to_account="gmail") == "kimm"

    def test_explicit_overrides_reply_inheritance(self) -> None:
        assert select_account("gmail", reply_to_account="kimm") == "gmail"

    @pytest.mark.parametrize("raw", [" gmail ", "kimm\n", "\tkimm"])
    def test_surrounding_whitespace_is_stripped(self, raw: str) -> None:
        assert select_account(raw) == raw.strip()


class TestReplyInheritance:
    def test_reply_inherits_kimm_thread_account(self) -> None:
        # KIMM regression: replies on an existing MailOn thread keep MailOn.
        assert select_account(None, reply_to_account="kimm") == "kimm"

    def test_reply_inherits_gmail_thread_account(self) -> None:
        assert select_account(None, reply_to_account="gmail") == "gmail"

    def test_reply_whitespace_is_stripped(self) -> None:
        assert select_account(None, reply_to_account=" gmail ") == "gmail"

    def test_invalid_reply_account_is_rejected(self) -> None:
        with pytest.raises(AccountSelectionError) as excinfo:
            select_account(None, reply_to_account="outlook")
        message = str(excinfo.value)
        assert "outlook" in message
        assert "gmail" in message
        assert "kimm" in message


class TestMissingAccount:
    def test_no_account_and_no_thread_refuses(self) -> None:
        with pytest.raises(AccountSelectionError) as excinfo:
            select_account(None)
        message = str(excinfo.value)
        assert "gmail" in message
        assert "kimm" in message

    def test_explicit_none_with_none_thread_refuses(self) -> None:
        with pytest.raises(AccountSelectionError):
            select_account(None, reply_to_account=None)


class TestInvalidAccount:
    @pytest.mark.parametrize(
        "raw",
        ["outlook", "mailon", "naver", "gmail.com", "gmail,kimm", "", "   "],
    )
    def test_unknown_value_is_rejected(self, raw: str) -> None:
        with pytest.raises(AccountSelectionError) as excinfo:
            select_account(raw)
        message = str(excinfo.value)
        assert "gmail" in message
        assert "kimm" in message

    @pytest.mark.parametrize("raw", ["GMAIL", "Gmail", "KIMM", "Kimm", "kImM"])
    def test_casing_variants_are_rejected_not_coerced(self, raw: str) -> None:
        # Fail-closed: no silent case coercion. Only exact lowercase is accepted.
        with pytest.raises(AccountSelectionError):
            select_account(raw)

    def test_rejected_value_is_echoed_back(self) -> None:
        with pytest.raises(AccountSelectionError) as excinfo:
            select_account("Gmail")
        assert "Gmail" in str(excinfo.value)


class TestContract:
    def test_error_is_a_value_error(self) -> None:
        assert issubclass(AccountSelectionError, ValueError)

    def test_accounts_constant_lists_both_accounts(self) -> None:
        assert set(mail_account_routing.ACCOUNTS) == {"gmail", "kimm"}
