from __future__ import annotations

import sys
from pathlib import Path
import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from skills.topics.scripts import topics_registry  # noqa: E402


def test_add_list_remove_persists_normalized_topic(tmp_path: Path) -> None:
    # Given
    state = tmp_path / "research-topics.yaml"

    # When
    added = topics_registry.add_topic(state, "  autophagy   flux ")

    # Then
    assert added.accepted is True
    assert topics_registry.list_topics(state) == ("autophagy flux",)
    assert topics_registry.add_topic(state, "AUTOPHAGY FLUX").duplicate is True
    assert topics_registry.remove_topic(state, "autophagy flux") is True
    assert topics_registry.list_topics(state) == ()


@pytest.mark.parametrize("topic", ["cell monitoring", "특허 patent 기밀", "prior art monitoring"])
def test_keywords_are_registered_like_other_topics(tmp_path: Path, topic: str) -> None:
    # Given
    state = tmp_path / "research-topics.yaml"

    # When
    result = topics_registry.add_topic(state, topic)

    # Then
    assert result.accepted is True
    assert topics_registry.list_topics(state) == (topic,)
    assert state.stat().st_mode & 0o777 == 0o600


def test_keyword_suggestion_is_accepted_without_registry_mutation(tmp_path: Path) -> None:
    # Given
    state = tmp_path / "research-topics.yaml"

    # When
    suggestion = topics_registry.validate_suggestion("prior art monitoring")

    # Then
    assert suggestion.accepted is True
    assert suggestion.topic == "prior art monitoring"
    assert topics_registry.list_topics(state) == ()
