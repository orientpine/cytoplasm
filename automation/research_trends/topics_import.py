from __future__ import annotations

from pathlib import Path


class TopicsScriptsOverrideError(ValueError):
    """TOPICS_SCRIPTS cannot identify an importable Python package."""


def topics_import_location(scripts_dir: Path) -> tuple[str, Path]:
    """Return the package name and sys.path root represented by scripts_dir."""
    if len(scripts_dir.parents) < 3:
        raise TopicsScriptsOverrideError(
            "TOPICS_SCRIPTS must contain at least three trailing Python identifiers "
            f"(for example, skills/topics/scripts): {scripts_dir}"
        )
    package_parts = scripts_dir.parts[-3:]
    if not all(part.isidentifier() for part in package_parts):
        raise TopicsScriptsOverrideError(
            "TOPICS_SCRIPTS must contain at least three trailing Python identifiers "
            f"(for example, skills/topics/scripts): {scripts_dir}"
        )
    return ".".join(package_parts), scripts_dir.parents[2]
