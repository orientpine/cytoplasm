"""Offline regression tests for long MailOn compose recipient probes.

The browser doubles in this module expose only ``_compose.getForm()``.  Any
attempt to exercise a send trigger fails the test, so these tests cannot send
mail or reach a network transport.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import pytest

from mailon import send_trigger


BODY = "offline compose probe marker"
LONG_RECIPIENTS = tuple(
    f"synthetic-recipient-{index:02d}-long-address@example.test"
    for index in range(11)
)


@dataclass
class NodeFormProbeBrowser:
    """Run the production probe JS against a synthetic in-memory form only."""

    form: dict[str, str]
    probe_calls: int = 0

    def eval_js(self, script: str) -> str:
        if "compose form probe" not in script:
            raise AssertionError("send transport is stubbed; only the form probe is allowed")
        self.probe_calls += 1
        program = (
            "const form = JSON.parse(process.argv[1]);"
            "global.window = {_compose: {getForm: () => form}};"
            f"const result = ({script});"
            "process.stdout.write(result);"
        )
        completed = subprocess.run(
            ["node", "-e", program, json.dumps(self.form)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout


@dataclass
class Legacy200ProbeBrowser:
    """Stub the historical probe contract that sliced every field at 200."""

    form: dict[str, str]
    probe_calls: int = 0

    def eval_js(self, script: str) -> str:
        if "compose form probe" not in script:
            raise AssertionError("send transport is stubbed; only the form probe is allowed")
        self.probe_calls += 1
        probed = {key: str(value)[:200] for key, value in self.form.items()}
        return json.dumps(json.dumps(probed))


def _form(to_value: str) -> dict[str, str]:
    return {
        "method": "send",
        "to": to_value,
        "from": "synthetic-sender@example.test",
        "content": BODY,
    }


def _verify(browser, recipients: tuple[str, ...]) -> tuple[bool, dict[str, str]]:
    ok, dump = send_trigger.verify_compose_form(browser, recipients, BODY)
    return ok, json.loads(dump)


def test_form_probe_preserves_all_11_long_synthetic_recipients():
    """Regression: the production JS must not hide recipients after char 200."""
    to_value = ",".join(LONG_RECIPIENTS)
    assert len(to_value) > 200
    browser = NodeFormProbeBrowser(_form(to_value))

    ok, dump = _verify(browser, LONG_RECIPIENTS)

    assert ok is True
    assert dump["to"] == to_value
    assert LONG_RECIPIENTS[-1] in dump["to"]
    assert browser.probe_calls == 1


def test_legacy_200_char_probe_omits_tail_and_fails_all_recipient_gate():
    """Reproduce the original 11-recipient failure without a live MailOn page."""
    to_value = ",".join(LONG_RECIPIENTS)
    browser = Legacy200ProbeBrowser(_form(to_value))

    ok, dump = _verify(browser, LONG_RECIPIENTS)

    assert len(dump["to"]) == 200
    assert LONG_RECIPIENTS[-1] not in dump["to"]
    assert ok is False
    assert browser.probe_calls == 1


@pytest.mark.parametrize(
    ("field_length", "expected_ok"),
    [(199, True), (200, True), (201, False)],
)
def test_legacy_probe_boundary_around_200_chars(field_length: int, expected_ok: bool):
    """At 201 chars the legacy dump loses the final recipient character."""
    recipient = "boundary-recipient@example.test"
    to_value = "x" * (field_length - len(recipient)) + recipient
    assert len(to_value) == field_length

    ok, dump = _verify(Legacy200ProbeBrowser(_form(to_value)), (recipient,))

    assert len(dump["to"]) == min(field_length, 200)
    assert ok is expected_ok


def test_legacy_probe_accepts_a_short_recipient_list_as_control():
    """The old implementation only failed after the recipient field grew long."""
    recipients = ("short-one@example.test", "short-two@example.test")
    to_value = ",".join(recipients)
    assert len(to_value) < 200

    ok, dump = _verify(Legacy200ProbeBrowser(_form(to_value)), recipients)

    assert dump["to"] == to_value
    assert ok is True
