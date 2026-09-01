from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))

wiki_store = import_module("wiki_store")


def _guide_meta() -> dict:
    _, _, frontmatter = wiki_store.SCHEMA_GUIDE.partition("---\n")
    header, separator, _ = frontmatter.partition("---\n")
    assert separator
    meta, _ = wiki_store.parse_note(f"---\n{header}---\n")
    return meta


def test_schema_guide_matches_validator_accepted_key_set() -> None:
    guide_meta = _guide_meta()
    documented_keys = set(guide_meta)
    accepted_keys = set(wiki_store.REQUIRED_KEYS) | set(wiki_store.TWIN_KEYS)

    assert set(wiki_store.TWIN_KEYS) <= documented_keys
    assert documented_keys == accepted_keys
    assert wiki_store.validate_meta(guide_meta) == []
