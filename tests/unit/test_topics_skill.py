from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from skills.topics.scripts import topics_registry  # noqa: E402


RULES = Path(__file__).resolve().parents[2] / "configs" / "sensitivity-rules.yaml"


def test_add_list_remove_persists_normalized_topic(tmp_path: Path) -> None:
    # Given
    state = tmp_path / "research-topics.yaml"
    rules = topics_registry.load_rules(RULES)

    # When
    added = topics_registry.add_topic(state, "  autophagy   flux ", rules)

    # Then
    assert added.accepted is True
    assert topics_registry.list_topics(state) == ("autophagy flux",)
    assert topics_registry.add_topic(state, "AUTOPHAGY FLUX", rules).duplicate is True
    assert topics_registry.remove_topic(state, "autophagy flux") is True
    assert topics_registry.list_topics(state) == ()


def test_sensitive_add_is_refused_and_never_written(tmp_path: Path) -> None:
    # Given
    state = tmp_path / "research-topics.yaml"
    rules = topics_registry.load_rules(RULES)

    # When
    result = topics_registry.add_topic(state, "patent landscaping", rules)

    # Then
    assert result.accepted is False
    assert result.guidance
    assert topics_registry.list_topics(state) == ()
    assert not state.exists()


def test_sensitive_auto_suggestion_is_refused_without_registry_mutation(tmp_path: Path) -> None:
    # Given
    state = tmp_path / "research-topics.yaml"
    rules = topics_registry.load_rules(RULES)

    # When
    suggestion = topics_registry.validate_suggestion("prior art monitoring", rules)

    # Then
    assert suggestion.accepted is False
    assert suggestion.guidance
    assert topics_registry.list_topics(state) == ()
