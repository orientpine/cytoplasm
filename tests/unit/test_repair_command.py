from __future__ import annotations

import pytest

from automation.repair.repair_command import parse_repair_command


@pytest.mark.parametrize("command", ["!repair", "수리해줘", "이상해"])
def test_parse_repair_command_accepts_supported_manual_triggers(command: str) -> None:
    # Given: a supported repair request form.

    # When: the command boundary parses it.
    parsed = parse_repair_command(command)

    # Then: it becomes a repair intent with a nonempty normalized message.
    assert parsed is not None
    assert parsed.message


def test_parse_repair_command_rejects_unrelated_text() -> None:
    # Given: ordinary conversation text.

    # When: it reaches the repair command boundary.
    parsed = parse_repair_command("오늘 날씨 알려줘")

    # Then: no repair ticket intent is created.
    assert parsed is None
