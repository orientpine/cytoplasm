from __future__ import annotations

from pathlib import Path

import pytest

from automation.research_trends.topics_import import (
    TopicsScriptsOverrideError,
    topics_import_location,
)


def test_topics_scripts_override_rejects_a_path_without_three_package_parts() -> None:
    with pytest.raises(
        TopicsScriptsOverrideError,
        match=r"TOPICS_SCRIPTS.*three.*Python identifiers",
    ):
        topics_import_location(Path("topics/scripts"))


def test_topics_scripts_override_resolves_a_valid_package_path() -> None:
    package, import_root = topics_import_location(Path("workspace/skills/topics/scripts"))

    assert package == "skills.topics.scripts"
    assert import_root == Path("workspace")
