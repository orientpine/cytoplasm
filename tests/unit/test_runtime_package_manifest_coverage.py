"""Runtime-package drift decisions cannot be added silently."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final


_REPO: Final = Path(__file__).resolve().parents[2]
_MANIFEST: Final = _REPO / "configs" / "runtime-package-manifest.txt"
_RUNTIME_PATH: Final = re.compile(
    r"(?:\.hermes/|\"\.hermes\"\s*/\s*\")([a-z0-9_]+_runtime)"
)
_COMMENT_DECISION: Final = re.compile(
    r"^#\s+(?P<package>[a-z0-9_]+_runtime):\s+reason=\S.+;\s+alternative=\S.+$"
)
_DEPLOY_ARCHIVE_FILES: Final = re.compile(
    r'deploy_archive_stream\s+"\$repo_root"\s+"\$repo_root/[^\"]+"\s+'
    + r"(?P<files>[A-Za-z0-9_. -]+?)\s+\\\n\s*\|\s*run_agent"
)


def _runtime_packages_referenced_by_automation() -> frozenset[str]:
    packages: set[str] = set()
    for path in _REPO.joinpath("automation").rglob("*"):
        if path.suffix not in {".py", ".sh"}:
            continue
        packages.update(_RUNTIME_PATH.findall(path.read_text(encoding="utf-8")))
    return frozenset(packages)


def test_every_automation_runtime_package_has_a_drift_decision() -> None:
    """A new runtime path needs a comparable row or a documented different check."""
    registered: set[str] = set()
    exceptions: set[str] = set()
    malformed_comments: list[str] = []

    for number, line in enumerate(_MANIFEST.read_text(encoding="utf-8").splitlines(), start=1):
        if not line or line.startswith("#"):
            if "_runtime:" in line:
                match = _COMMENT_DECISION.fullmatch(line)
                if match is None:
                    malformed_comments.append(f"{number}: {line}")
                else:
                    exceptions.add(match.group("package"))
            continue
        fields = line.split("|")
        assert len(fields) in {4, 6, 7}, f"invalid manifest row {number}: {line}"
        runtime_parts = Path(fields[2]).parts
        if ".hermes" in runtime_parts:
            registered.add(runtime_parts[runtime_parts.index(".hermes") + 1])

    packages = _runtime_packages_referenced_by_automation()
    assert packages, "runtime-package inventory unexpectedly found no automation paths"
    assert not malformed_comments, "invalid exception comment(s):\n" + "\n".join(malformed_comments)
    undecided = sorted(packages - registered - exceptions)
    assert not undecided, (
        "automation runtime package(s) lack a manifest row or reason+alternative comment:\n"
        + "\n".join(undecided)
    )


def _deployer_file_list(source: str) -> tuple[str, ...] | None:
    deployer = _REPO / source / "deploy.sh"
    if not deployer.is_file():
        return None
    match = _DEPLOY_ARCHIVE_FILES.search(deployer.read_text(encoding="utf-8"))
    if match is None:
        return None
    return tuple(match.group("files").split())


def test_registered_deployer_file_lists_match_the_manifest() -> None:
    """A partial deploy must constrain the release snapshot to its shipped files."""
    for number, line in enumerate(_MANIFEST.read_text(encoding="utf-8").splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        deployed = _deployer_file_list(fields[1])
        if deployed is None:
            continue
        manifest_files = tuple(fields[6].split(",")) if len(fields) == 7 else ()
        assert manifest_files == deployed, (
            f"manifest file list differs from deployer arguments at {fields[1]}/deploy.sh "
            f"for manifest line {number}: expected {deployed}, got {manifest_files}"
        )
