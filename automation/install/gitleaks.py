"""Pinned integrity verification for the gitleaks binary the installer fetches.

F6 (security audit, 2026-08-15). ``SystemMutator._install_gitleaks`` downloaded
the release tarball with ``curl -fsSL`` and installed it to
``/usr/local/bin/gitleaks`` as root with no integrity check whatsoever — TLS was
the only control, in a project whose entire update story is signed-release
provenance. Whoever can serve that URL (a compromised release asset, a CDN, an
account takeover) got root code execution on every install.

Pinning the digest here moves that trust decision into the repository, where it
is reviewed once and thereafter immutable: a later swap of the published asset
no longer changes what a fresh install runs.

Digests are the publisher's ``gitleaks_<version>_checksums.txt`` values. The
``linux_x64`` entry was additionally re-verified by downloading the artifact and
hashing it (2026-08-15).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Final

#: (version, release architecture suffix) -> sha256 of the .tar.gz release asset.
GITLEAKS_ARCHIVE_SHA256: Final[Mapping[tuple[str, str], str]] = {
    ("8.30.1", "x64"): "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
    ("8.30.1", "arm64"): "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080",
}


class GitleaksIntegrityError(OSError):
    """The gitleaks archive is not the pinned release artifact; installation stops.

    Deliberately an ``OSError`` so ``install.executor`` renders it as a FAIL
    result and the installer exits non-zero, rather than escaping as a traceback.
    """


def expected_archive_sha256(version: str, archive_arch: str) -> str:
    """Return the pinned digest, or refuse to install an unpinned combination.

    Fail-closed: bumping the gitleaks version without recording its digest must
    stop the install, not silently fall back to "download and trust".
    """
    digest = GITLEAKS_ARCHIVE_SHA256.get((version, archive_arch))
    if digest is None:
        raise GitleaksIntegrityError(
            f"no pinned gitleaks checksum for version {version} on {archive_arch}"
        )
    return digest


def verify_archive(archive: Path, expected: str) -> str:
    """Hash the downloaded archive and raise unless it matches ``expected``."""
    try:
        with archive.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        raise GitleaksIntegrityError(
            f"gitleaks archive cannot be read for verification: {archive}"
        ) from error
    if actual != expected:
        raise GitleaksIntegrityError(
            f"gitleaks archive checksum mismatch: expected {expected}, got {actual}"
        )
    return actual
