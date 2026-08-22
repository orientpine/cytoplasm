"""Overlap rotation: two trust keys must be installable at the same time.

Rotating the update-trust key is only safe if every node trusts the *new* key
before a release is cut with it, which means both keys have to live in
`/etc/autophagy/update-allowed-signers` for the length of the transition. The
file format, `parse_allowed_signers`, `render_allowed_signers` and the
`any(...)` in `_check_content` have always accepted several entries — the CLI
was the one place that did not, because `plan_signer_install` rendered the whole
file from a single entry and silently dropped whatever was installed.

Getting the order wrong is quiet: a node that no longer trusts the signing key
answers `UPDATE-TRUST-BLOCK` with rc 0, so nothing alarms and the installation
simply stops taking updates.

Ownership is recorded rather than performed (the `chown` to root:root needs
privilege), matching `test_install_trust_key_bootstrap.py`'s harness.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from automation.install.allowed_signers import (
    UPDATE_TRUST_TARGET,
    SignerInstallRequest,
    TrustKeyError,
    fingerprint,
    parse_allowed_signers,
    parse_public_key,
    plan_signer_install,
)
from automation.install.trust_file import RealFilesystem, apply_install, read_existing
from automation.install.trust_key_bootstrap import main, plan_install


class RecordingFilesystem:
    """Real reads/writes under tmp_path; `chown` recorded instead of performed."""

    def __init__(self) -> None:
        self.real = RealFilesystem()

    def lstat(self, path: Path) -> os.stat_result:
        return self.real.lstat(path)

    def read_text(self, path: Path) -> str:
        return self.real.read_text(path)

    def write_atomic(self, path: Path, content: str, mode: int) -> None:
        self.real.write_atomic(path, content, mode)

    def set_ownership(self, path: Path, uid: int, gid: int) -> None:
        del path, uid, gid


def _key_line(seed: int, comment: str) -> str:
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm
    blob += (32).to_bytes(4, "big") + bytes((seed + index) % 256 for index in range(32))
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}"


_OLD = _key_line(1, "old-key")
_NEW = _key_line(90, "new-key")


def _materials(text: str) -> set[str]:
    return {entry.key.material for entry in parse_allowed_signers(text)}


def _install(target: Path, key: str, *, merge: bool) -> RecordingFilesystem:
    filesystem = RecordingFilesystem()
    existing = read_existing(target, filesystem) if merge else ""
    apply_install(plan_install(key, path=target, existing=existing), filesystem)
    return filesystem


def test_add_preserves_the_installed_entry_and_both_parse(tmp_path: Path) -> None:
    # Given an installed old key
    target = tmp_path / "etc" / "update-allowed-signers"
    _ = _install(target, _OLD, merge=False)
    assert len(parse_allowed_signers(target.read_text(encoding="utf-8"))) == 1

    # When the new key is merged in rather than installed over
    _ = _install(target, _NEW, merge=True)

    # Then both keys are trusted for the length of the transition
    text = target.read_text(encoding="utf-8")
    assert len(parse_allowed_signers(text)) == 2
    assert _materials(text) == {parse_public_key(_OLD).material, parse_public_key(_NEW).material}


def test_install_without_add_still_replaces_the_whole_file(tmp_path: Path) -> None:
    # Given — replacement is how a rotation *ends*, so it must stay reachable
    target = tmp_path / "etc" / "update-allowed-signers"
    _ = _install(target, _OLD, merge=False)

    # When
    _ = _install(target, _NEW, merge=False)

    # Then
    assert _materials(target.read_text(encoding="utf-8")) == {parse_public_key(_NEW).material}


def test_add_is_idempotent_for_a_key_that_is_already_trusted(tmp_path: Path) -> None:
    # Given the installer is re-run, which is the documented recovery move
    target = tmp_path / "etc" / "update-allowed-signers"
    _ = _install(target, _OLD, merge=False)

    # When
    _ = _install(target, _OLD, merge=True)

    # Then the entry is not duplicated
    assert len(parse_allowed_signers(target.read_text(encoding="utf-8"))) == 1


def test_add_on_a_fresh_host_installs_the_single_entry(tmp_path: Path) -> None:
    # Given nothing installed yet — --add must not require a predecessor
    target = tmp_path / "etc" / "update-allowed-signers"

    # When
    _ = _install(target, _OLD, merge=True)

    # Then
    assert len(parse_allowed_signers(target.read_text(encoding="utf-8"))) == 1


def test_merged_plan_reports_the_new_key_fingerprint(tmp_path: Path) -> None:
    # Given — --expect-fingerprint compares the *bundled* key, not the file
    del tmp_path
    request = SignerInstallRequest(
        _NEW,
        "update-trust@autophagy",
        UPDATE_TRUST_TARGET,
        existing=f"update-trust@autophagy {_OLD}\n",
    )

    # When
    plan = plan_signer_install(request)

    # Then
    assert plan.fingerprint == fingerprint(parse_public_key(_NEW))
    assert len(parse_allowed_signers(plan.content)) == 2


def test_merge_refuses_a_trust_file_it_cannot_read() -> None:
    # Given a header-only (half-written) trust file — fail closed, do not guess
    request = SignerInstallRequest(
        _NEW,
        "update-trust@autophagy",
        UPDATE_TRUST_TARGET,
        existing="# only a header\n",
    )

    # When / Then
    with pytest.raises(TrustKeyError, match="SIGNERS-EMPTY"):
        _ = plan_signer_install(request)


def test_read_existing_is_empty_when_nothing_is_installed(tmp_path: Path) -> None:
    assert read_existing(tmp_path / "absent", RealFilesystem()) == ""


def test_cli_add_dry_run_plans_both_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an installed old key and a bundled new one
    target = tmp_path / "etc" / "update-allowed-signers"
    _ = _install(target, _OLD, merge=False)
    key_file = tmp_path / "new.pub"
    _ = key_file.write_text(f"{_NEW}\n", encoding="utf-8")

    # When
    code = main(["install", "--key", str(key_file), "--path", str(target), "--add", "--dry-run"])

    # Then the printed plan carries both keys, and nothing was written
    output = capsys.readouterr().out
    assert code == 0
    assert parse_public_key(_OLD).material in output
    assert parse_public_key(_NEW).material in output
    assert _materials(target.read_text(encoding="utf-8")) == {parse_public_key(_OLD).material}


def test_cli_dry_run_without_add_plans_only_the_new_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    target = tmp_path / "etc" / "update-allowed-signers"
    _ = _install(target, _OLD, merge=False)
    key_file = tmp_path / "new.pub"
    _ = key_file.write_text(f"{_NEW}\n", encoding="utf-8")

    # When
    code = main(["install", "--key", str(key_file), "--path", str(target), "--dry-run"])

    # Then
    output = capsys.readouterr().out
    assert code == 0
    assert parse_public_key(_OLD).material not in output
    assert parse_public_key(_NEW).material in output
