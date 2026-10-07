"""Public-release de-identification guards for tracked sources (non-test unless a test says so)."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation import peer_attestation, skill_gate

_REPO: Final = Path(__file__).resolve().parents[2]
# A snowflake is a standalone decimal id. Without the boundaries this also matched
# any 17-digit run inside a longer token — a 40-character git object name reaches
# that length often enough that pinning an engine revision failed the gate.
_DISCORD_SNOWFLAKE: Final = re.compile(rb"(?<![0-9A-Za-z])[0-9]{17,19}(?![0-9A-Za-z])")
_KOREAN_PERSON_NAME: Final = re.compile(
    r"(?:안녕하세요[,.]?\s*|이름(?:은)?\s+|\")([가-힣]{2,4})(?:입니다|\s+올림|이라면)"
)
_INSTITUTIONAL_EMAIL: Final = re.compile(
    r"@[A-Za-z0-9.-]+\.(?:ac|re|go)\.kr\b",
    re.IGNORECASE,
)
_HOST_TOKEN: Final = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._-]{2,}\b")
_PRIVATE_HOST_DIGESTS: Final = frozenset(
    {
        "6fced41a9fd917ed7f46603f8d0691d0cb6ca32edcb4126e7f6fdee13de17aa1",
        "0da7e438ace623b0ef31a143c2570c632432b726da6c3ece9b0ea1e0686fad5d",
    }
)
# Private workstation accounts and the owner's real name are digests for the same reason as the
# hosts: a public guard must not publish what it forbids. 2026-10-01: the v1.14.0 public tree
# carried both — a prompt asset, two source comments, feature docs and test fixtures.
_HOME_DIRECTORY: Final = re.compile(r"/(?:home|Users)/([A-Za-z0-9._-]+)/")
_PRIVATE_ACCOUNT_DIGESTS: Final = frozenset(
    {
        "d602c74b86c96b8e2b64b0500d96506e4bad4e9300e5af7137a4f11724565341",
        "55e9541d006ea239e5cc53b37d73630351e5612af636bdcfd56829ec84e6dc68",
    }
)
_HANGUL_RUN: Final = re.compile(r"[가-힣]{2,}")
_PRIVATE_NAME_DIGESTS: Final = frozenset(
    {"e296cec0fe73da9c83c66badd33180fe65ce129fd89fc5bcd0db66c8393b8cf6"}
)
_SYNTHETIC_SNOWFLAKE: Final = (
    b"123456789012345678"  # Synthetic shape fixture, not a real Discord ID.
)
_SYNTHETIC_PEER_SNOWFLAKE: Final = "234567890123456789"  # Synthetic peer fixture.

_ALLOWLISTED_PREFIXES: Final = (
    ".omo/",  # Private orchestration records are omitted by the public-export contract.
    "docs/qa/",  # Raw and masked execution evidence is omitted by the public-export contract.
    "tests/",  # Test fixtures deliberately use synthetic snowflake-shaped values.
)
_ALLOWLISTED_PATH_PARTS: Final = (
    "/vendor/",  # Byte-preserved upstream vendoring is not an editable project source.
)
_ALLOWLISTED_SUFFIXES: Final = (
    "/scenario.sh",  # Skill scenario scripts are executable test fixtures outside tests/.
    "/uv.lock",  # Generated dependency metadata contains hashes and numeric build tags.
)
_ALLOWLISTED_MATCHES: Final[dict[tuple[str, int], str]] = {
    # Add only an unavoidable, non-identifying literal with a path+line and a safety reason.
}
_PUBLIC_EXPORT_MANIFEST: Final = _REPO / "configs" / "public-export-manifest.txt"


def _tracked_paths() -> tuple[Path, ...]:
    listing = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=_REPO,
        capture_output=True,
        check=True,
    ).stdout
    return tuple(Path(raw.decode("utf-8")) for raw in listing.split(b"\0") if raw)


def _is_allowlisted_path(path: Path) -> bool:
    relative = path.as_posix()
    return (
        relative.startswith(_ALLOWLISTED_PREFIXES)
        or any(part in relative for part in _ALLOWLISTED_PATH_PARTS)
        or relative.endswith(_ALLOWLISTED_SUFFIXES)
    )


def _public_export_exclusions() -> tuple[str, ...]:
    return tuple(
        line
        for raw in _PUBLIC_EXPORT_MANIFEST.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    )


def _is_private_export_path(path: Path, exclusions: tuple[str, ...]) -> bool:
    relative = path.as_posix()
    return any(
        (entry.endswith("/") and relative.startswith(entry)) or relative == entry
        for entry in exclusions
    )


def _contains_private_hostname(line: str) -> bool:
    return any(
        hashlib.sha256(candidate.group().lower().encode()).hexdigest()
        in _PRIVATE_HOST_DIGESTS
        for candidate in _HOST_TOKEN.finditer(line)
    )


def _names_private_account_home(line: str) -> bool:
    return any(
        hashlib.sha256(home.group(1).encode()).hexdigest() in _PRIVATE_ACCOUNT_DIGESTS
        for home in _HOME_DIRECTORY.finditer(line)
    )


def _names_owner(line: str) -> bool:
    # A Korean name usually carries a particle (`…인지`, `…에게`), so the run's prefixes are checked.
    return any(
        hashlib.sha256(run[:size].encode()).hexdigest() in _PRIVATE_NAME_DIGESTS
        for run in _HANGUL_RUN.findall(line)
        for size in range(2, min(len(run), 4) + 1)
    )


def _write_peer_registry(path: Path) -> None:
    payload = "\n".join(
        (
            "peers:",
            "  owner-agent:",
            "    account: agent",
            f"    bot_user_id: {_SYNTHETIC_SNOWFLAKE.decode()}",
            "  peer-agent:",
            "    account: peer",
            f"    bot_user_id: {_SYNTHETIC_PEER_SNOWFLAKE}",
            "",
        )
    )
    _ = path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)


def _trust_test_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        peer_attestation,
        "_trusted_owner_uids",
        lambda: frozenset({os.getuid()}),
        raising=False,
    )


def test_discord_snowflake_shape_fixture_is_synthetic() -> None:
    # Given: a clearly synthetic value with Discord's decimal snowflake shape.
    # When: the release-hygiene matcher evaluates it.
    matched = _DISCORD_SNOWFLAKE.fullmatch(_SYNTHETIC_SNOWFLAKE)

    # Then: the matcher covers the intended shape without embedding a production identifier.
    assert matched is not None


def test_tracked_non_test_sources_contain_no_discord_snowflake_literals() -> None:
    # Given: every tracked path except explicitly documented non-release/test artifacts.
    findings: list[str] = []

    # When: every line is checked without reproducing matched identifiers in test output.
    for relative in _tracked_paths():
        if _is_allowlisted_path(relative):
            continue
        source = _REPO / relative
        if not source.is_file():
            continue
        for line_number, line in enumerate(source.read_bytes().splitlines(), start=1):
            if _DISCORD_SNOWFLAKE.search(line) is None:
                continue
            key = (relative.as_posix(), line_number)
            if key not in _ALLOWLISTED_MATCHES:
                findings.append(f"{relative}:{line_number}")

    # Then: no public source carries a Discord-shaped literal outside the reviewed allowlist.
    assert not findings, "Discord snowflake-shaped literals found:\n" + "\n".join(findings)


def test_public_non_test_sources_contain_no_personal_or_private_infra_literals() -> None:
    # Given: every editable non-test file that survives the public-export manifest.
    exclusions = _public_export_exclusions()
    findings: list[str] = []

    # When: generalized identity patterns and private-host digests scan each line.
    for relative in _tracked_paths():
        if _is_allowlisted_path(relative) or _is_private_export_path(relative, exclusions):
            continue
        source = _REPO / relative
        if not source.is_file():
            continue
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            matched = (
                _KOREAN_PERSON_NAME.search(line) is not None
                or _INSTITUTIONAL_EMAIL.search(line) is not None
                or _contains_private_hostname(line)
            )
            if matched:
                findings.append(f"{relative}:{line_number}")

    # Then: the public source set contains no matching personal or infrastructure value.
    assert not findings, "Public PII/infrastructure literals found:\n" + "\n".join(findings)


def test_public_files_including_tests_name_no_private_account_or_owner() -> None:
    # Given: every file the public export ships. Tests count — a comment or fixture in a public
    #        test is published exactly like a doc line. Vendored trees are skipped because
    #        automation/public_export_redaction.py de-identifies them at export time.
    exclusions = _public_export_exclusions()
    findings: list[str] = []

    # When: each line is checked for a private account's home directory and the owner's name.
    for relative in _tracked_paths():
        if _is_private_export_path(relative, exclusions) or "/vendor/" in relative.as_posix():
            continue
        source = _REPO / relative
        if not source.is_file():
            continue
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            if _names_private_account_home(line) or _names_owner(line):
                findings.append(f"{relative}:{line_number}")

    # Then: nothing the public repository receives names the owner or a private account.
    assert not findings, "Private account home or owner name found:\n" + "\n".join(findings)


def test_load_bot_ids_returns_none_for_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid registry reached through a symbolic link in a trusted test directory.
    _trust_test_owner(monkeypatch)
    registry = tmp_path / "registry.yaml"
    _write_peer_registry(registry)
    link = tmp_path / "peers.yaml"
    link.symlink_to(registry)

    # When / Then: the trust-root loader refuses the symlink itself.
    assert peer_attestation.load_bot_ids(link) is None


def test_load_bot_ids_returns_none_for_a_world_writable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an otherwise valid registry writable by other users.
    _trust_test_owner(monkeypatch)
    registry = tmp_path / "peers.yaml"
    _write_peer_registry(registry)
    registry.chmod(0o602)

    # When / Then: the writable trust root is rejected.
    assert peer_attestation.load_bot_ids(registry) is None


def test_load_bot_ids_returns_none_for_a_group_writable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an otherwise valid registry writable by its group.
    _trust_test_owner(monkeypatch)
    registry = tmp_path / "peers.yaml"
    _write_peer_registry(registry)
    registry.chmod(0o620)

    # When / Then: the writable trust root is rejected.
    assert peer_attestation.load_bot_ids(registry) is None


def test_load_bot_ids_returns_none_for_a_non_root_or_ops_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid registry whose owner is outside the trusted root-or-ops set.
    registry = tmp_path / "peers.yaml"
    _write_peer_registry(registry)
    monkeypatch.setattr(
        peer_attestation,
        "_trusted_owner_uids",
        frozenset,
        raising=False,
    )

    # When / Then: ownership ambiguity fails closed.
    assert peer_attestation.load_bot_ids(registry) is None


def test_load_bot_ids_returns_none_for_a_writable_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a protected file inside a directory that other users can replace entries in.
    _trust_test_owner(monkeypatch)
    parent = tmp_path / "trust-root"
    parent.mkdir(mode=0o700)
    registry = parent / "peers.yaml"
    _write_peer_registry(registry)
    parent.chmod(0o707)

    # When / Then: the parent-directory boundary is rejected.
    assert peer_attestation.load_bot_ids(registry) is None


def test_load_bot_ids_returns_none_for_a_non_regular_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a directory at the configured registry path.
    _trust_test_owner(monkeypatch)
    registry = tmp_path / "peers.yaml"
    registry.mkdir(mode=0o700)

    # When / Then: only regular files can supply attestation identities.
    assert peer_attestation.load_bot_ids(registry) is None


def test_attestation_trust_root_never_resolves_below_hermes_home() -> None:
    # Given: every production Python call to the shared bot-id loader.
    calls: list[tuple[str, str]] = []
    for relative in _tracked_paths():
        if relative.suffix != ".py" or not relative.as_posix().startswith(
            ("automation/", "skills/")
        ):
            continue
        source = (_REPO / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id == "load_bot_ids" and node.args:
                calls.append((relative.as_posix(), ast.unparse(node.args[0])))

    # When / Then: the only configured trust root is system-owned, never ~/.hermes.
    assert calls == [("automation/skill_gate.py", "OPS_PEERS_CONFIG")]
    assert skill_gate.OPS_PEERS_CONFIG == Path("/etc/autophagy/peers.yaml")
    assert all(".hermes" not in argument for _, argument in calls)
