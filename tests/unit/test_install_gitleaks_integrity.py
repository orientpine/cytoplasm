"""Regression: the gitleaks binary is checksum-pinned before it is installed.

F6 (security audit, 2026-08-15). ``SystemMutator._install_gitleaks`` fetched the
release tarball with ``curl -fsSL`` and installed it into ``/usr/local/bin`` as
root with zero integrity verification — TLS was the only control, in a project
whose whole update story is signed-release provenance.

Nothing here downloads or installs anything: the subprocess runner is replaced
with a fake that records argv and simulates ``curl`` by writing bytes the test
chooses.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
from pathlib import Path

import pytest

from automation.install.apply import SystemMutator
from automation.install.gitleaks import (
    GITLEAKS_ARCHIVE_SHA256,
    GitleaksIntegrityError,
    expected_archive_sha256,
    verify_archive,
)
from automation.install.plan import InstallGitleaks
from automation.node_config import default_node_config

PINNED_VERSION = "8.30.1"
GENUINE = b"pretend this is the real gitleaks release tarball\n"
TAMPERED = b"pretend this is a swapped release asset\n"


class FakeRun:
    """Records argv; simulates curl by writing ``payload`` to the -o target."""

    def __init__(self, payload: bytes) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._payload: bytes = payload

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        _cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(command))
        if command[0] == "curl":
            _ = Path(command[command.index("-o") + 1]).write_bytes(self._payload)
        return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls]


def _current_arch() -> str:
    return {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64"}[platform.machine()]


# --------------------------------------------------------------------------
# The pin itself.
# --------------------------------------------------------------------------


def test_every_pinned_digest_is_a_sha256_hex_string() -> None:
    assert GITLEAKS_ARCHIVE_SHA256, "the pin registry must not be empty"
    for (version, arch), digest in GITLEAKS_ARCHIVE_SHA256.items():
        assert version and arch
        assert len(digest) == 64
        assert digest == digest.lower()
        assert all(character in "0123456789abcdef" for character in digest)


def test_the_installer_default_version_is_pinned_for_every_supported_arch() -> None:
    # The installer's default must never be an unpinned combination — including
    # on whatever machine is running this suite.
    for arch in ("x64", "arm64", _current_arch()):
        assert expected_archive_sha256(PINNED_VERSION, arch)


def test_an_unpinned_version_is_refused() -> None:
    with pytest.raises(GitleaksIntegrityError):
        _ = expected_archive_sha256("9.99.9", "x64")


def test_an_unpinned_architecture_is_refused() -> None:
    with pytest.raises(GitleaksIntegrityError):
        _ = expected_archive_sha256(PINNED_VERSION, "riscv64")


# --------------------------------------------------------------------------
# verify_archive.
# --------------------------------------------------------------------------


def test_verify_archive_accepts_the_matching_digest(tmp_path: Path) -> None:
    archive = tmp_path / "gitleaks.tar.gz"
    _ = archive.write_bytes(GENUINE)

    assert verify_archive(archive, hashlib.sha256(GENUINE).hexdigest()) == (
        hashlib.sha256(GENUINE).hexdigest()
    )


def test_verify_archive_rejects_a_swapped_artifact(tmp_path: Path) -> None:
    archive = tmp_path / "gitleaks.tar.gz"
    _ = archive.write_bytes(TAMPERED)

    with pytest.raises(GitleaksIntegrityError) as caught:
        _ = verify_archive(archive, hashlib.sha256(GENUINE).hexdigest())

    assert "checksum mismatch" in str(caught.value)


def test_verify_archive_rejects_a_missing_download(tmp_path: Path) -> None:
    with pytest.raises(GitleaksIntegrityError):
        _ = verify_archive(tmp_path / "absent.tar.gz", "0" * 64)


# --------------------------------------------------------------------------
# The installer action.
# --------------------------------------------------------------------------


def test_a_checksum_mismatch_stops_before_extraction_and_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the download does not match the pinned digest (a swapped asset).
    runner = FakeRun(TAMPERED)
    mutator = SystemMutator(default_node_config())
    monkeypatch.setattr(mutator, "run", runner)

    # When/Then: the installer refuses to proceed.
    with pytest.raises(GitleaksIntegrityError):
        mutator.apply(InstallGitleaks(PINNED_VERSION))

    # And: nothing from the archive was unpacked or placed on PATH.
    assert runner.verbs() == ["curl"]
    assert "tar" not in runner.verbs()
    assert "install" not in runner.verbs()


def test_a_checksum_mismatch_is_an_oserror_so_the_installer_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # install.executor renders OSError as a FAIL result; a plain ValueError
    # would escape as a traceback and skip the FAIL accounting.
    runner = FakeRun(TAMPERED)
    mutator = SystemMutator(default_node_config())
    monkeypatch.setattr(mutator, "run", runner)

    with pytest.raises(OSError):
        mutator.apply(InstallGitleaks(PINNED_VERSION))


def test_an_unpinned_version_never_even_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = FakeRun(GENUINE)
    mutator = SystemMutator(default_node_config())
    monkeypatch.setattr(mutator, "run", runner)

    with pytest.raises(GitleaksIntegrityError):
        mutator.apply(InstallGitleaks("9.99.9"))

    assert runner.calls == []


def test_a_matching_archive_is_extracted_without_inheriting_tar_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the pinned digest matches what the download produced. The pin is
    # substituted rather than the payload faked, so the real registry stays
    # authoritative for the assertions above.
    payload = GENUINE

    def pinned(_version: str, _arch: str) -> str:
        return hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr("automation.install.apply.expected_archive_sha256", pinned)
    runner = FakeRun(payload)
    mutator = SystemMutator(default_node_config())
    monkeypatch.setattr(mutator, "run", runner)

    mutator.apply(InstallGitleaks(PINNED_VERSION))

    assert runner.verbs() == ["curl", "tar", "install"]
    tar_argv = next(call for call in runner.calls if call[0] == "tar")
    assert "--no-same-owner" in tar_argv
    assert "--no-same-permissions" in tar_argv
    install_argv = next(call for call in runner.calls if call[0] == "install")
    assert install_argv[-1] == "/usr/local/bin/gitleaks"
