"""Tests for the update-trust-key bootstrap.

Nothing here touches `/etc/autophagy` and nothing requires root: the write path
runs against `tmp_path` and ownership is recorded rather than applied, while the
ownership *policy* is exercised by handing `verify_installed` the uid/gid sets it
would use in production.
"""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

import pytest

from automation.install.checks import Status
from automation.install.trust_key_bootstrap import (
    DEFAULT_UPDATE_TRUST_PRINCIPAL,
    GIT_SIGNATURE_NAMESPACE,
    MANAGED_SKILLS_ALLOWED_SIGNERS_PATH,
    REQUIRED_MODE,
    UPDATE_ALLOWED_SIGNERS_PATH,
    InstallPlan,
    PublicKey,
    RealFilesystem,
    SignerEntry,
    TrustKeyError,
    apply_install,
    fingerprint,
    fingerprints_match,
    main,
    parse_allowed_signers,
    parse_public_key,
    plan_install,
    render_allowed_signers,
    verify_installed,
)


def _ssh_string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _ed25519_material(seed: bytes = bytes(range(32))) -> str:
    """Build a syntactically real ssh-ed25519 public key blob from a fixed seed."""
    return base64.b64encode(_ssh_string(b"ssh-ed25519") + _ssh_string(seed)).decode("ascii")


VECTOR_MATERIAL = _ed25519_material()
VECTOR_KEY_LINE = f"ssh-ed25519 {VECTOR_MATERIAL} autophagy-test-vector"
# Ground truth: `ssh-keygen -lf` on this exact key prints this fingerprint. The
# constant is external evidence, not a value produced by the code under test.
VECTOR_FINGERPRINT = "SHA256:ZkAslGjFiUHdGf/WUL8rQvkib4PTvQatUV0OUQSncCA"


class RecordingFilesystem:
    """Real reads/writes under tmp_path; `chown` recorded instead of performed."""

    def __init__(self) -> None:
        self.real = RealFilesystem()
        self.ownership: list[tuple[Path, int, int]] = []
        self.order: list[str] = []

    def lstat(self, path: Path) -> os.stat_result:
        return self.real.lstat(path)

    def read_text(self, path: Path) -> str:
        return self.real.read_text(path)

    def write_atomic(self, path: Path, content: str, mode: int) -> None:
        self.order.append("write")
        self.real.write_atomic(path, content, mode)

    def set_ownership(self, path: Path, uid: int, gid: int) -> None:
        self.order.append("chown")
        self.ownership.append((path, uid, gid))


def _current_ids() -> tuple[frozenset[int], frozenset[int]]:
    return frozenset({os.getuid()}), frozenset({os.getgid()})


def _install(tmp_path: Path) -> tuple[Path, RecordingFilesystem, InstallPlan]:
    target = tmp_path / "etc" / "update-allowed-signers"
    filesystem = RecordingFilesystem()
    plan = plan_install(VECTOR_KEY_LINE, path=target)
    apply_install(plan, filesystem)
    return target, filesystem, plan


def test_fingerprint_matches_ssh_keygen() -> None:
    assert fingerprint(parse_public_key(VECTOR_KEY_LINE)) == VECTOR_FINGERPRINT


def test_fingerprint_comparison_tolerates_surrounding_whitespace() -> None:
    assert fingerprints_match(VECTOR_FINGERPRINT, f"  {VECTOR_FINGERPRINT}\n")
    assert not fingerprints_match(VECTOR_FINGERPRINT, VECTOR_FINGERPRINT[:-1] + "X")


def test_parse_keeps_algorithm_material_and_comment() -> None:
    key = parse_public_key(f"# a comment line\n{VECTOR_KEY_LINE}\n")
    assert key == PublicKey("ssh-ed25519", VECTOR_MATERIAL, "autophagy-test-vector")


@pytest.mark.parametrize(
    ("text", "marker"),
    [
        ("", "BUNDLED-KEY-NOT-SINGLE"),
        (f"{VECTOR_KEY_LINE}\n{VECTOR_KEY_LINE}", "BUNDLED-KEY-NOT-SINGLE"),
        ("ssh-ed25519", "BUNDLED-KEY-MALFORMED"),
        (f"ssh-dss {VECTOR_MATERIAL}", "BUNDLED-KEY-ALGORITHM"),
        ("ssh-ed25519 not-base64!!", "BUNDLED-KEY-BASE64"),
        (f"ssh-rsa {VECTOR_MATERIAL}", "BUNDLED-KEY-MISMATCH"),
        (f"ssh-ed25519 {base64.b64encode(b'ab').decode()}", "BUNDLED-KEY-TRUNCATED"),
    ],
)
def test_unusable_bundled_keys_are_refused(text: str, marker: str) -> None:
    with pytest.raises(TrustKeyError, match=marker):
        parse_public_key(text)


def test_plan_targets_root_owned_0644_by_default() -> None:
    plan = plan_install(VECTOR_KEY_LINE)
    assert (plan.path, plan.uid, plan.gid, plan.mode) == (UPDATE_ALLOWED_SIGNERS_PATH, 0, 0, REQUIRED_MODE)
    assert plan.principal == DEFAULT_UPDATE_TRUST_PRINCIPAL
    assert plan.fingerprint == VECTOR_FINGERPRINT


def test_plan_scopes_the_key_to_the_git_signature_namespace() -> None:
    # A key scoped to any other namespace makes `git verify-tag` reject it, which
    # would stall every update on every node (verified against git 2.x).
    assert f'namespaces="{GIT_SIGNATURE_NAMESPACE}"' in plan_install(VECTOR_KEY_LINE).content


def test_plan_states_the_two_key_distinction_in_the_installed_file() -> None:
    content = plan_install(VECTOR_KEY_LINE).content
    assert "UPDATE TRUST" in content
    assert str(MANAGED_SKILLS_ALLOWED_SIGNERS_PATH) in content


def test_plan_refuses_to_write_into_the_group_skill_signers_file() -> None:
    with pytest.raises(TrustKeyError, match="WRONG-FILE"):
        plan_install(VECTOR_KEY_LINE, path=MANAGED_SKILLS_ALLOWED_SIGNERS_PATH)


@pytest.mark.parametrize("principal", ["", "two tokens"])
def test_plan_refuses_an_unusable_principal(principal: str) -> None:
    with pytest.raises(TrustKeyError, match="PRINCIPAL-INVALID"):
        plan_install(VECTOR_KEY_LINE, principal=principal)


def test_empty_signers_file_is_refused() -> None:
    with pytest.raises(TrustKeyError, match="SIGNERS-EMPTY"):
        render_allowed_signers(())


def test_rendered_signers_round_trip() -> None:
    entry = SignerEntry(DEFAULT_UPDATE_TRUST_PRINCIPAL, parse_public_key(VECTOR_KEY_LINE))
    assert parse_allowed_signers(render_allowed_signers((entry,))) == (entry,)


def test_signers_file_of_only_comments_is_refused() -> None:
    with pytest.raises(TrustKeyError, match="SIGNERS-EMPTY"):
        parse_allowed_signers("# nothing but a comment\n")


def test_apply_writes_then_hands_ownership_to_root(tmp_path: Path) -> None:
    target, filesystem, plan = _install(tmp_path)
    assert filesystem.order == ["write", "chown"]
    assert filesystem.ownership == [(target, 0, 0)]
    assert stat.S_IMODE(target.lstat().st_mode) == REQUIRED_MODE
    assert target.read_text(encoding="utf-8") == plan.content


def test_verify_accepts_a_correctly_installed_key(tmp_path: Path) -> None:
    target, filesystem, _ = _install(tmp_path)
    uids, gids = _current_ids()

    results = verify_installed(
        target, filesystem, expected_fingerprint=VECTOR_FINGERPRINT, trusted_uids=uids, trusted_gids=gids
    )

    assert [result.status for result in results] == [Status.PASS, Status.PASS]


def test_verify_reports_a_deleted_trust_key_as_fail_closed(tmp_path: Path) -> None:
    (result,) = verify_installed(tmp_path / "absent", RecordingFilesystem())
    assert result.status is Status.FAIL
    assert "TRUST-KEY-MISSING" in result.detail


def test_verify_rejects_a_symlinked_trust_key(tmp_path: Path) -> None:
    target, filesystem, _ = _install(tmp_path)
    link = tmp_path / "etc" / "link-signers"
    link.symlink_to(target)
    uids, gids = _current_ids()

    shape, _ = verify_installed(link, filesystem, trusted_uids=uids, trusted_gids=gids)

    assert shape.status is Status.FAIL
    assert "TRUST-KEY-NOT-A-FILE" in shape.detail


@pytest.mark.parametrize("mode", [0o664, 0o666])
def test_verify_rejects_a_writable_trust_key(tmp_path: Path, mode: int) -> None:
    target, filesystem, _ = _install(tmp_path)
    target.chmod(mode)
    uids, gids = _current_ids()

    shape, _ = verify_installed(target, filesystem, trusted_uids=uids, trusted_gids=gids)

    assert shape.status is Status.FAIL
    assert "TRUST-KEY-WRITABLE" in shape.detail


def test_verify_rejects_a_writable_parent_directory(tmp_path: Path) -> None:
    target, filesystem, _ = _install(tmp_path)
    target.parent.chmod(0o775)
    uids, gids = _current_ids()

    shape, _ = verify_installed(target, filesystem, trusted_uids=uids, trusted_gids=gids)

    assert shape.status is Status.FAIL
    assert "TRUST-KEY-WRITABLE" in shape.detail


def test_verify_rejects_an_unreadable_mode(tmp_path: Path) -> None:
    target, filesystem, _ = _install(tmp_path)
    target.chmod(0o600)
    uids, gids = _current_ids()

    shape, _ = verify_installed(target, filesystem, trusted_uids=uids, trusted_gids=gids)

    assert shape.status is Status.FAIL
    assert "TRUST-KEY-WRONG-MODE" in shape.detail


def test_verify_rejects_a_non_root_owner_by_default(tmp_path: Path) -> None:
    target, filesystem, _ = _install(tmp_path)

    shape, _ = verify_installed(target, filesystem)

    assert shape.status is Status.FAIL
    assert "TRUST-KEY-WRONG-OWNER" in shape.detail


def test_verify_rejects_a_fingerprint_that_differs_from_the_published_one(tmp_path: Path) -> None:
    target, filesystem, _ = _install(tmp_path)
    uids, gids = _current_ids()

    _, content = verify_installed(
        target,
        filesystem,
        expected_fingerprint="SHA256:published-value-from-the-release-notes",
        trusted_uids=uids,
        trusted_gids=gids,
    )

    assert content.status is Status.FAIL
    assert "TRUST-KEY-FINGERPRINT-MISMATCH" in content.detail


def test_verify_without_a_published_fingerprint_warns_and_prints_the_installed_one(tmp_path: Path) -> None:
    target, filesystem, _ = _install(tmp_path)
    uids, gids = _current_ids()

    _, content = verify_installed(target, filesystem, trusted_uids=uids, trusted_gids=gids)

    assert content.status is Status.WARN
    assert VECTOR_FINGERPRINT in content.detail


def test_verify_rejects_a_corrupted_signers_file(tmp_path: Path) -> None:
    target, filesystem, _ = _install(tmp_path)
    target.write_text("update-trust@autophagy ssh-ed25519 not-base64!!\n", encoding="utf-8")
    target.chmod(REQUIRED_MODE)
    uids, gids = _current_ids()

    _, content = verify_installed(target, filesystem, trusted_uids=uids, trusted_gids=gids)

    assert content.status is Status.FAIL
    assert "TRUST-KEY-UNREADABLE" in content.detail


def test_cli_fingerprint_prints_the_published_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_file = tmp_path / "update-trust-key.pub"
    key_file.write_text(f"{VECTOR_KEY_LINE}\n", encoding="utf-8")

    assert main(["fingerprint", "--key", str(key_file)]) == 0
    assert capsys.readouterr().out.strip() == VECTOR_FINGERPRINT


def test_cli_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key_file = tmp_path / "update-trust-key.pub"
    key_file.write_text(f"{VECTOR_KEY_LINE}\n", encoding="utf-8")
    target = tmp_path / "etc" / "update-allowed-signers"

    assert main(["install", "--key", str(key_file), "--path", str(target), "--dry-run"]) == 0
    assert not target.exists()
    assert VECTOR_FINGERPRINT in capsys.readouterr().out


def test_cli_refuses_to_install_a_bundle_that_fails_the_published_fingerprint(tmp_path: Path) -> None:
    key_file = tmp_path / "update-trust-key.pub"
    key_file.write_text(f"{VECTOR_KEY_LINE}\n", encoding="utf-8")
    target = tmp_path / "etc" / "update-allowed-signers"

    code = main(
        [
            "install",
            "--key",
            str(key_file),
            "--path",
            str(target),
            "--expect-fingerprint",
            "SHA256:a-different-published-value",
        ]
    )

    assert code == 1
    assert not target.exists()


def test_cli_missing_bundle_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["install", "--key", str(tmp_path / "absent.pub")]) == 1
    assert "BUNDLED-KEY-MISSING" in capsys.readouterr().err


def test_cli_verify_of_an_absent_trust_key_is_non_zero(tmp_path: Path) -> None:
    assert main(["verify", "--path", str(tmp_path / "absent")]) == 1
