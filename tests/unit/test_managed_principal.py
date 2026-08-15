from __future__ import annotations

import re

import pytest

from automation import skill_store
from automation.group_roster import validator as roster_validator
from automation.managed_skills import principal


def _pattern(module: object, name: str) -> str:
    """Read one compiled pattern, including deliberately private conformance anchors."""
    value: object = getattr(module, name)
    assert isinstance(value, re.Pattern)
    return value.pattern


def test_publisher_principal_pattern_when_compared_then_equals_roster_validator_pattern() -> None:
    # Given: roster schema v1 already fixed the group publisher principal format (W-F2-A).
    # When: the managed-skill channel declares the shared format.
    # Then: both accept exactly the same strings — there is only ONE principal format.
    assert _pattern(principal, "PUBLISHER_PRINCIPAL") == _pattern(
        roster_validator, "_PUBLISHER_PRINCIPAL"
    )


def test_publisher_name_pattern_when_compared_then_equals_skill_store_pattern() -> None:
    # Given: the privileged managed installer validates the manifest publisher name.
    # When: the publisher-side config validates the same field before publishing.
    # Then: a name accepted at publish time is accepted at install time.
    assert _pattern(principal, "PUBLISHER_NAME") == _pattern(skill_store, "_PUBLISHER_NAME")


@pytest.mark.parametrize(
    "value",
    (
        "publisher-testlab@autophagy",
        "publisher-example-admin@autophagy",
        "publisher-a@autophagy",
        "publisher-a1@autophagy",
    ),
)
def test_is_publisher_principal_when_value_is_well_formed_then_accepts(value: str) -> None:
    assert principal.is_publisher_principal(value)


@pytest.mark.parametrize(
    "value",
    (
        "",
        "   ",
        "publisher-TESTLAB@autophagy",
        "publisher-@autophagy",
        "publisher-testlab-@autophagy",
        "testlab@autophagy",
        "publisher-testlab@example.invalid",
        "publisher-testlab@autophagy extra",
        "publisher-testlab@autophagy\n",
    ),
)
def test_is_publisher_principal_when_value_is_malformed_then_rejects(value: str) -> None:
    assert not principal.is_publisher_principal(value)


@pytest.mark.parametrize("value", ("testlab", "example-admin", "a", "a" * 32))
def test_is_publisher_name_when_value_is_well_formed_then_accepts(value: str) -> None:
    assert principal.is_publisher_name(value)


@pytest.mark.parametrize("value", ("", "Testlab", "-testlab", "test lab", "a" * 33))
def test_is_publisher_name_when_value_is_malformed_then_rejects(value: str) -> None:
    assert not principal.is_publisher_name(value)
