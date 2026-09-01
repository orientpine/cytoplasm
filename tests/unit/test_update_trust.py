from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.install.trust_key_bootstrap import (
    DEFAULT_UPDATE_TRUST_PRINCIPAL,
    GIT_SIGNATURE_NAMESPACE,
    UPDATE_ALLOWED_SIGNERS_PATH,
)
from automation.update_trust import (
    UPDATE_ALLOWED_SIGNERS_PATH as VERIFIER_ALLOWED_SIGNERS_PATH,
)
from automation.update_trust import (
    UPDATE_TRUST_PRINCIPAL,
    UpdateTrustError,
    resolve_signed_update,
    resolve_update_target,
)
from automation import update_trust as update_trust_module
from automation.node_config import NodeConfig
from automation.update_trust import main as update_trust_main
from automation.update_trust_state import (
    ReleaseFloorError,
    load_release_floor,
    parse_release_version,
    privileged_advance_release_floor,
    refuse_release_rollback,
    release_floor,
    release_floor_path,
    save_release_floor,
)


@dataclass(frozen=True, slots=True)
class UpdateRepository:
    publisher: Path
    mirror: Path
    remote: Path
    allowed_signers: Path
    old_key: Path
    new_key: Path


def test_update_trust_constants_match_the_installed_bootstrap_contract() -> None:
    assert VERIFIER_ALLOWED_SIGNERS_PATH == UPDATE_ALLOWED_SIGNERS_PATH
    assert UPDATE_TRUST_PRINCIPAL == DEFAULT_UPDATE_TRUST_PRINCIPAL


def _run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _write_allowed_signers(path: Path, key: Path) -> None:
    public_key = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    line = f'{DEFAULT_UPDATE_TRUST_PRINCIPAL} namespaces="{GIT_SIGNATURE_NAMESPACE}" {public_key}\n'
    _ = path.write_text(line, encoding="utf-8")


def _commit(repository: UpdateRepository, content: str) -> str:
    _ = (repository.publisher / "release.txt").write_text(content, encoding="utf-8")
    _ = _run("git", "add", "release.txt", cwd=repository.publisher)
    _ = _run("git", "commit", "-m", content, cwd=repository.publisher)
    return _run("git", "rev-parse", "HEAD", cwd=repository.publisher)


def _sign_tag(repository: UpdateRepository, tag: str, key: Path) -> None:
    _ = _run(
        "git",
        "-c",
        "gpg.format=ssh",
        "-c",
        f"user.signingKey={key}",
        "tag",
        "-s",
        tag,
        "-m",
        f"release {tag}",
        cwd=repository.publisher,
    )


@pytest.fixture
def update_repository(tmp_path: Path) -> UpdateRepository:
    remote = tmp_path / "public.git"
    publisher = tmp_path / "publisher"
    mirror = tmp_path / "node-mirror"
    old_key = tmp_path / "update-old"
    new_key = tmp_path / "update-new"
    allowed_signers = tmp_path / "update-allowed-signers"

    _ = _run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(old_key))
    _ = _run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(new_key))
    _ = _run("git", "init", "--bare", str(remote))
    _ = _run("git", "init", str(publisher))
    _ = _run("git", "config", "user.name", "Update fixture", cwd=publisher)
    _ = _run("git", "config", "user.email", DEFAULT_UPDATE_TRUST_PRINCIPAL, cwd=publisher)
    _ = _run("git", "remote", "add", "origin", str(remote), cwd=publisher)
    repository = UpdateRepository(
        publisher=publisher,
        mirror=mirror,
        remote=remote,
        allowed_signers=allowed_signers,
        old_key=old_key,
        new_key=new_key,
    )
    _ = _commit(repository, "initial")
    _ = _run("git", "branch", "-M", "main", cwd=publisher)
    _ = _run("git", "push", "-u", "origin", "main", cwd=publisher)
    _ = _run("git", "clone", "--branch", "main", str(remote), str(mirror))
    _write_allowed_signers(allowed_signers, old_key)
    return repository


@pytest.fixture
def floor(tmp_path: Path) -> Path:
    """A migrated authoritative floor, outside every checkout (C1)."""
    path = tmp_path / "root-state" / "release-floor.json"
    save_release_floor(path, release_floor("v0.0.0", "0" * 40))
    return path


def test_resolve_signed_update_when_main_advances_without_tag_then_blocks_unsigned_head(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: the previous public commit was released under a trusted signed tag.
    _sign_tag(update_repository, "v1.0.0", update_repository.old_key)
    _ = _run("git", "push", "origin", "refs/tags/v1.0.0", cwd=update_repository.publisher)
    _ = _commit(update_repository, "unsigned update")
    _ = _run("git", "push", "origin", "main", cwd=update_repository.publisher)

    # When/Then: mutable main alone cannot become a convergence target.
    with pytest.raises(UpdateTrustError, match=r"^UNSIGNED-HEAD:"):
        _ = resolve_signed_update(
            update_repository.mirror,
            update_repository.allowed_signers,
            floor_path=floor,
        )


def test_resolve_signed_update_when_current_head_has_trusted_tag_then_returns_tag_commit(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: public main and an annotated release tag are pushed together.
    expected = _run("git", "rev-parse", "HEAD", cwd=update_repository.publisher)
    _sign_tag(update_repository, "v1.0.0", update_repository.old_key)
    _ = _run(
        "git", "push", "origin", "main", "refs/tags/v1.0.0", cwd=update_repository.publisher
    )

    # When: the node resolves the trusted update.
    release = resolve_signed_update(
        update_repository.mirror,
        update_repository.allowed_signers,
        floor_path=floor,
    )

    # Then: the target comes from the verified tag's peeled commit.
    assert release.tag == "v1.0.0"
    assert release.commit_sha == expected


def test_resolve_signed_update_when_trust_rotates_then_rejects_old_key_signature(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: the current release tag was signed by the formerly trusted key.
    _sign_tag(update_repository, "v1.0.0", update_repository.old_key)
    _ = _run(
        "git", "push", "origin", "main", "refs/tags/v1.0.0", cwd=update_repository.publisher
    )
    assert resolve_signed_update(
        update_repository.mirror,
        update_repository.allowed_signers,
        floor_path=floor,
    ).tag == "v1.0.0"
    _write_allowed_signers(update_repository.allowed_signers, update_repository.new_key)

    # When/Then: replacing allowed_signers removes the old key's authority immediately.
    with pytest.raises(UpdateTrustError, match=r"^BAD-SIGNATURE:"):
        _ = resolve_signed_update(
            update_repository.mirror,
            update_repository.allowed_signers,
            floor_path=floor,
        )


def test_resolve_signed_update_when_head_has_lightweight_tag_then_rejects_bare_commit(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: main has a tag-shaped ref but no signed annotated tag object.
    _ = _run("git", "tag", "v1.0.0", cwd=update_repository.publisher)
    _ = _run(
        "git", "push", "origin", "main", "refs/tags/v1.0.0", cwd=update_repository.publisher
    )

    # When/Then: a lightweight tag cannot turn a bare commit into a trusted release.
    with pytest.raises(UpdateTrustError, match=r"^UNSIGNED-HEAD:"):
        _ = resolve_signed_update(
            update_repository.mirror,
            update_repository.allowed_signers,
            floor_path=floor,
        )


def test_resolve_update_target_when_node_explicitly_opts_out_then_returns_mutable_main(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: an unsigned public head and an explicit transitional opt-out.
    expected = _run("git", "rev-parse", "HEAD", cwd=update_repository.publisher)

    # When: the dangerous compatibility policy resolves its target.
    target = resolve_update_target(
        update_repository.mirror,
        require_signed_updates=False,
        allowed_signers=update_repository.allowed_signers,
        floor_path=floor,
    )

    # Then: only this explicit branch keeps the former mutable-main behavior.
    assert target == expected

def test_resolve_signed_cli_reads_no_node_policy_and_blocks_an_unsigned_head(
    update_repository: UpdateRepository,
    floor: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The verb an automated ROOT install may run must take no policy input at all.

    `resolve` above still honours `require_signed_updates`, which is exactly why the
    privileged helper must not call it: the file that answer came from was named under
    the ops account's own home, and ops holds NOPASSWD sudo for that helper. This verb
    reads no configuration, so nothing an unprivileged account can write reaches the
    authorisation decision.
    """

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the signature-only verb must not read a node configuration")

    monkeypatch.setattr(update_trust_module, "load_node_config", _refuse)

    status = update_trust_main(
        [
            "resolve-signed",
            "--mirror",
            str(update_repository.mirror),
            "--allowed-signers",
            str(update_repository.allowed_signers),
            "--floor-path",
            str(floor),
        ]
    )

    assert status == 1
    assert "UPDATE-TRUST-BLOCK" in capsys.readouterr().err


def test_resolve_signed_cli_returns_the_commit_of_a_trusted_release_tag(
    update_repository: UpdateRepository,
    floor: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the public head is the peel target of a tag signed by the trusted principal.
    expected = _run("git", "rev-parse", "HEAD", cwd=update_repository.publisher)
    _sign_tag(update_repository, "v1.0.0", update_repository.old_key)
    _ = _run(
        "git", "push", "origin", "main", "refs/tags/v1.0.0", cwd=update_repository.publisher
    )

    # When: the signature-only verb resolves the target.
    status = update_trust_main(
        [
            "resolve-signed",
            "--mirror",
            str(update_repository.mirror),
            "--allowed-signers",
            str(update_repository.allowed_signers),
            "--floor-path",
            str(floor),
        ]
    )

    # Then: it prints the commit but leaves the root-owned floor unchanged.
    assert status == 0
    assert capsys.readouterr().out.strip() == expected
    assert load_release_floor(floor) == release_floor("v0.0.0", "0" * 40)


def test_resolve_update_target_when_channel_is_set_then_uses_it_without_changing_origin(
    update_repository: UpdateRepository,
    tmp_path: Path,
) -> None:
    # Given: origin and an explicit update channel publish different trusted releases.
    origin_sha = _run("git", "rev-parse", "HEAD", cwd=update_repository.publisher)
    _sign_tag(update_repository, "v1.0.0", update_repository.old_key)
    _ = _run(
        "git", "push", "origin", "main", "refs/tags/v1.0.0", cwd=update_repository.publisher
    )
    channel = tmp_path / "channel.git"
    _ = _run("git", "init", "--bare", str(channel))
    _ = _run("git", "remote", "add", "update-channel", str(channel), cwd=update_repository.publisher)
    channel_sha = _commit(update_repository, "channel release")
    _sign_tag(update_repository, "v2.0.0", update_repository.old_key)
    _ = _run(
        "git",
        "push",
        "update-channel",
        "main",
        "refs/tags/v2.0.0",
        cwd=update_repository.publisher,
    )

    # When: the resolver receives the roster's non-null update channel.
    channel_floor = tmp_path / "channel-floor.json"
    origin_floor = tmp_path / "origin-floor.json"
    for path in (channel_floor, origin_floor):
        save_release_floor(path, release_floor("v0.0.0", "0" * 40))
    selected = resolve_update_target(
        update_repository.mirror,
        require_signed_updates=True,
        allowed_signers=update_repository.allowed_signers,
        remote_url=str(channel),
        floor_path=channel_floor,
    )

    # Then: it selects that channel without mutating the default origin behavior or config.
    assert selected == channel_sha
    assert resolve_update_target(
        update_repository.mirror,
        require_signed_updates=True,
        allowed_signers=update_repository.allowed_signers,
        floor_path=origin_floor,
    ) == origin_sha
    assert _run("git", "remote", "get-url", "origin", cwd=update_repository.mirror) == str(
        update_repository.remote
    )


# --- C1: rollback to a genuinely signed older release ------------------------------
#
# Signatures prove AUTHORSHIP, never FRESHNESS. Every test below starts from a
# tag the real update-trust key really signed, so signature, principal, and
# TAG-RACE all pass; only the age is wrong.


def _publish(repository: UpdateRepository, content: str, tag: str) -> str:
    sha = _commit(repository, content)
    _sign_tag(repository, tag, repository.old_key)
    _ = _run("git", "push", "origin", "main", f"refs/tags/{tag}", cwd=repository.publisher)
    return sha


def _rewind_origin(repository: UpdateRepository, sha: str) -> None:
    """What an origin compromise can do WITHOUT any signing key."""
    _ = _run(
        "git", "push", "--force", str(repository.remote), f"{sha}:refs/heads/main",
        cwd=repository.publisher,
    )


def test_resolve_signed_update_when_origin_rewinds_to_an_older_signed_tag_then_refuses(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: the node has already verified v2.0.0, and v1.0.0 is still validly signed.
    old_sha = _publish(update_repository, "OLD-1.0.0", "v1.0.0")
    _ = _publish(update_repository, "NEW-2.0.0", "v2.0.0")
    verified = resolve_signed_update(
        update_repository.mirror,
        update_repository.allowed_signers,
        floor_path=floor,
    )
    assert verified.tag == "v2.0.0"
    privileged_advance_release_floor(floor, verified.tag, verified.commit_sha)

    # When: an attacker who cannot sign anything rewinds main onto the old release.
    _rewind_origin(update_repository, old_sha)

    # Then: authenticity is not enough — the release must also advance.
    with pytest.raises(UpdateTrustError, match=r"^RELEASE-ROLLBACK:"):
        _ = resolve_signed_update(
            update_repository.mirror,
            update_repository.allowed_signers,
            floor_path=floor,
        )


def test_resolve_signed_update_when_the_same_release_resolves_twice_then_both_paths_agree(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: one published release and the dual verification design (W-F1-D) —
    # the ops pre-gate resolves it, then the root helper resolves it again, and
    # the reconciler timer repeats the pair every two minutes.
    expected = _publish(update_repository, "NEW-1.4.0", "v1.4.0")

    # When/Then: re-resolving the SAME release at the SAME commit stays accepted.
    # A strictly-greater-per-resolution rule would refuse the second half of
    # every convergence and freeze the node after its first successful update.
    first = resolve_signed_update(
        update_repository.mirror, update_repository.allowed_signers, floor_path=floor
    )
    privileged_advance_release_floor(floor, first.tag, first.commit_sha)
    for _tick in range(5):
        assert resolve_signed_update(
            update_repository.mirror,
            update_repository.allowed_signers,
            floor_path=floor,
        ).commit_sha == expected
    pinned = load_release_floor(floor)
    assert pinned is not None
    assert (pinned.tag, pinned.commit_sha) == ("v1.4.0", expected)


def test_resolve_signed_update_when_a_newer_release_lands_then_the_floor_advances(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: a verified release, then a genuine forward publication.
    _ = _publish(update_repository, "NEW-1.0.0", "v1.0.0")
    initial = resolve_signed_update(
        update_repository.mirror, update_repository.allowed_signers, floor_path=floor
    )
    privileged_advance_release_floor(floor, initial.tag, initial.commit_sha)
    forward = _publish(update_repository, "NEW-1.0.1", "v1.0.1")

    # When: root re-verifies and explicitly advances after the read-only pre-gate.
    advanced = resolve_signed_update(
        update_repository.mirror, update_repository.allowed_signers, floor_path=floor
    )
    privileged_advance_release_floor(floor, advanced.tag, advanced.commit_sha)

    # Then: normal upgrades are unaffected and the anchor moves with them.
    assert (advanced.tag, advanced.commit_sha) == ("v1.0.1", forward)
    pinned = load_release_floor(floor)
    assert pinned is not None
    assert pinned.tag == "v1.0.1"


def test_resolve_signed_update_when_no_floor_exists_yet_then_the_first_release_bootstraps(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: a fresh installation has no legacy or authoritative floor.
    expected = _publish(update_repository, "FIRST-1.0.0", "v1.0.0")
    floor.unlink()

    # When: ops performs its read-only verification, then root re-verifies and seeds.
    trusted = resolve_signed_update(
        update_repository.mirror, update_repository.allowed_signers, floor_path=floor
    )
    assert not floor.exists()
    privileged_advance_release_floor(floor, trusted.tag, trusted.commit_sha)

    # Then: first convergence succeeds and leaves the authoritative anchor in place.
    assert trusted.commit_sha == expected
    assert floor.stat().st_mode & 0o777 == 0o644
    assert load_release_floor(floor) == release_floor("v1.0.0", expected)


def test_resolve_signed_update_when_an_unsigned_tag_claims_a_higher_version_then_it_cannot_pin(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: at the same head, a tag signed by an untrusted key claims v9.0.0
    # while the genuine release tag is v1.0.0. Candidates are tried newest-name
    # first, so the forged one is examined before the real one.
    expected = _commit(update_repository, "release")
    _sign_tag(update_repository, "v9.0.0", update_repository.new_key)
    _sign_tag(update_repository, "v1.0.0", update_repository.old_key)
    _ = _run(
        "git", "push", "origin", "main", "refs/tags/v9.0.0", "refs/tags/v1.0.0",
        cwd=update_repository.publisher,
    )

    # When: the node resolves.
    trusted = resolve_signed_update(
        update_repository.mirror, update_repository.allowed_signers, floor_path=floor
    )

    # Then: the unprivileged verifier accepts the genuine candidate but writes nothing.
    assert trusted.commit_sha == expected
    assert load_release_floor(floor) == release_floor("v0.0.0", "0" * 40)


def test_resolve_signed_update_when_the_release_tag_is_not_semver_then_refuses(
    update_repository: UpdateRepository,
    floor: Path,
) -> None:
    # Given: a properly signed tag whose name the release procedure could never emit.
    _ = _commit(update_repository, "release")
    _sign_tag(update_repository, "release-1.0.0", update_repository.old_key)
    _ = _run(
        "git", "push", "origin", "main", "refs/tags/release-1.0.0",
        cwd=update_repository.publisher,
    )

    # When/Then: an unorderable name fails closed instead of crashing or passing.
    with pytest.raises(UpdateTrustError, match=r"^RELEASE-VERSION:"):
        _ = resolve_signed_update(
            update_repository.mirror, update_repository.allowed_signers, floor_path=floor
        )
    assert load_release_floor(floor) == release_floor("v0.0.0", "0" * 40)


@pytest.mark.parametrize(
    "corruption",
    [
        pytest.param("", id="empty"),
        pytest.param("{", id="truncated-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"schema_version":1,"tag":"v1.0.0"}', id="missing-field"),
        pytest.param(
            '{"schema_version":1,"tag":"v1.0.0","commit_sha":"a"*40,"extra":1}',
            id="unknown-field",
        ),
        pytest.param(
            '{"schema_version":true,"tag":"v1.0.0","commit_sha":"' + "a" * 40 + '"}',
            id="bool-schema-version",
        ),
        pytest.param(
            '{"schema_version":2,"tag":"v1.0.0","commit_sha":"' + "a" * 40 + '"}',
            id="future-schema-version",
        ),
        pytest.param(
            '{"schema_version":1,"tag":null,"commit_sha":"' + "a" * 40 + '"}',
            id="null-tag",
        ),
        pytest.param(
            '{"schema_version":1,"tag":"HEAD","commit_sha":"' + "a" * 40 + '"}',
            id="unorderable-tag",
        ),
        pytest.param(
            '{"schema_version":1,"tag":"v1.0.0","commit_sha":"not-a-sha"}',
            id="bad-commit",
        ),
    ],
)
def test_resolve_signed_update_when_the_floor_file_is_corrupt_then_fails_closed(
    update_repository: UpdateRepository,
    floor: Path,
    corruption: str,
) -> None:
    # Given: a floor file that exists but cannot be understood. Reading that as
    # "no floor yet" would let one corrupted byte reopen the rollback window.
    _ = _publish(update_repository, "NEW-1.0.0", "v1.0.0")
    floor.parent.mkdir(parents=True, exist_ok=True)
    _ = floor.write_text(corruption, encoding="utf-8")

    # When/Then: the update is refused, not silently re-bootstrapped.
    with pytest.raises(UpdateTrustError, match=r"^RELEASE-FLOOR:"):
        _ = resolve_signed_update(
            update_repository.mirror, update_repository.allowed_signers, floor_path=floor
        )


def test_resolve_update_target_when_a_channel_switch_goes_backwards_then_refuses(
    update_repository: UpdateRepository,
    floor: Path,
    tmp_path: Path,
) -> None:
    # Given: the node verified v2.0.0 from origin, and a second channel that
    # publishes an older — but genuinely signed — v1.0.0.
    _ = _publish(update_repository, "ORIGIN-2.0.0", "v2.0.0")
    verified_sha = resolve_update_target(
        update_repository.mirror,
        require_signed_updates=True,
        allowed_signers=update_repository.allowed_signers,
        floor_path=floor,
    )
    privileged_advance_release_floor(floor, "v2.0.0", verified_sha)
    channel = tmp_path / "lagging-channel.git"
    _ = _run("git", "init", "--bare", str(channel))
    _ = _run("git", "remote", "add", "lagging", str(channel), cwd=update_repository.publisher)
    _ = _run("git", "reset", "--hard", "HEAD~1", cwd=update_repository.publisher)
    _sign_tag(update_repository, "v1.0.0", update_repository.old_key)
    _ = _run(
        "git", "push", "lagging", "HEAD:refs/heads/main", "refs/tags/v1.0.0",
        cwd=update_repository.publisher,
    )

    # When/Then: the floor belongs to the INSTALLATION, not to a channel. Being
    # repointed at a lagging feed is exactly the outcome it exists to refuse.
    with pytest.raises(UpdateTrustError, match=r"^RELEASE-ROLLBACK:"):
        _ = resolve_update_target(
            update_repository.mirror,
            require_signed_updates=True,
            allowed_signers=update_repository.allowed_signers,
            remote_url=str(channel),
            floor_path=floor,
        )


def test_update_trust_cli_reports_a_rollback_through_the_existing_block_channel(
    update_repository: UpdateRepository,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a node configuration that requires signed updates and owns a
    # private root, plus a floor already at v2.0.0.
    old_sha = _publish(update_repository, "OLD-1.0.0", "v1.0.0")
    new_sha = _publish(update_repository, "NEW-2.0.0", "v2.0.0")
    private_root = tmp_path / "private"
    node_config = tmp_path / "node.toml"
    _ = node_config.write_text(
        f'require_signed_updates = true\nprivate_root = "{private_root}"\n',
        encoding="utf-8",
    )
    authoritative = tmp_path / "root-state" / "release-floor.json"
    save_release_floor(authoritative, release_floor("v2.0.0", new_sha))
    def authoritative_path(_config: NodeConfig) -> Path:
        return authoritative

    monkeypatch.setattr(update_trust_module, "release_floor_path", authoritative_path)
    argv = [
        "resolve",
        "--mirror", str(update_repository.mirror),
        "--allowed-signers", str(update_repository.allowed_signers),
        "--node-config", str(node_config),
    ]
    assert update_trust_main(argv) == 0
    assert capsys.readouterr().out.strip() == new_sha

    # When: origin is rewound onto the old signed release and the CLI runs again.
    _rewind_origin(update_repository, old_sha)
    assert update_trust_main(argv) == 1

    # Then: the refusal reaches operators on the path main() already owned, so
    # the root-owned helper's `|| die "SYNC-BLOCK"` sees a non-zero exit.
    captured = capsys.readouterr()
    assert "UPDATE-TRUST-BLOCK RELEASE-ROLLBACK:" in captured.err
    assert old_sha not in captured.out


# --- C1: the floor primitives ------------------------------------------------------


def test_release_floor_path_is_the_single_location_both_verifiers_derive() -> None:
    # The ops pre-gate and the root-owned helper must anchor to ONE file; a
    # floor only one of them can see leaves the other exactly as exposed.
    from automation import deploy_reconcile_cli
    from automation.node_config import default_node_config, load_node_config

    config = default_node_config()
    assert release_floor_path(config) == Path(
        "/var/lib/autophagy/update-trust/release-floor.json"
    )
    # The ops pre-gate resolves the fixed root-owned path, separate from its
    # writable update-channel binding.
    assert deploy_reconcile_cli.RELEASE_FLOOR == release_floor_path(load_node_config())
    assert deploy_reconcile_cli.RELEASE_FLOOR.parent != deploy_reconcile_cli.UPDATE_CHANNEL_STATE.parent


@pytest.mark.parametrize(
    "tag",
    [
        "1.0.0", "v1.0", "v1.0.0.0", "v1.0.0-", "vX.Y.Z", "release-1.0.0",
        "v1.0.0 ", " v1.0.0", "v1.0.0\nv9.9.9", "v-1.0.0", "V1.0.0", "",
        "v" + "9" * 200 + ".0.0",
    ],
)
def test_parse_release_version_refuses_every_name_the_export_cannot_emit(tag: str) -> None:
    with pytest.raises(ReleaseFloorError, match=r"^RELEASE-VERSION:"):
        _ = parse_release_version(tag)


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.0.0", (0, 0, 0)),
        ("v1.2.3", (1, 2, 3)),
        ("v10.20.30", (10, 20, 30)),
        ("v1.2.3-rc1", (1, 2, 3)),
        ("v1.2.3+build.5", (1, 2, 3)),
    ],
)
def test_parse_release_version_orders_the_shapes_public_export_accepts(
    tag: str, expected: tuple[int, int, int]
) -> None:
    assert parse_release_version(tag) == expected


def test_refuse_release_rollback_accepts_only_the_identical_release_at_equal_version() -> None:
    sha, other = "a" * 40, "b" * 40
    pinned = release_floor("v1.2.3", sha)

    # Re-resolving the same release is the steady state, not an attack.
    refuse_release_rollback(pinned, release_floor("v1.2.3", sha))
    refuse_release_rollback(pinned, release_floor("v1.2.4", other))
    refuse_release_rollback(pinned, release_floor("v2.0.0", other))
    refuse_release_rollback(None, release_floor("v0.0.1", sha))

    # A version number reappearing on another object is substitution, not replay.
    with pytest.raises(ReleaseFloorError, match=r"^RELEASE-ROLLBACK:"):
        refuse_release_rollback(pinned, release_floor("v1.2.3", other))
    # A pre-release shares its triple with the final, so neither advances the other.
    with pytest.raises(ReleaseFloorError, match=r"^RELEASE-ROLLBACK:"):
        refuse_release_rollback(pinned, release_floor("v1.2.3-rc1", sha))
    for older in ("v1.2.2", "v1.1.9", "v0.9.9"):
        with pytest.raises(ReleaseFloorError, match=r"^RELEASE-ROLLBACK:"):
            refuse_release_rollback(pinned, release_floor(older, other))


def test_release_floor_refuses_a_commit_that_is_not_an_object_id() -> None:
    for commit in ("", "abc", "z" * 40, "a" * 39, "A" * 40, "a" * 41):
        with pytest.raises(ReleaseFloorError, match=r"^RELEASE-FLOOR:"):
            _ = release_floor("v1.0.0", commit)


def test_save_release_floor_refuses_to_write_inside_a_git_checkout(tmp_path: Path) -> None:
    # Same active guard as managed_sync.state._refuse_checkout_path: runtime
    # state inside a checkout makes ops trees dirty and blocks every ff-pull.
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    nested = checkout / "automation" / "release-floor.json"

    with pytest.raises(ReleaseFloorError, match=r"outside a git checkout"):
        save_release_floor(nested, release_floor("v1.0.0", "a" * 40))
    assert not nested.exists()


def test_save_release_floor_round_trips_atomically_without_leaving_temporaries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "release-floor.json"
    save_release_floor(path, release_floor("v1.0.0", "a" * 40))
    save_release_floor(path, release_floor("v2.0.0", "b" * 40))

    reloaded = load_release_floor(path)
    assert reloaded is not None
    assert (reloaded.tag, reloaded.commit_sha, reloaded.ordering) == ("v2.0.0", "b" * 40, (2, 0, 0))
    assert sorted(p.name for p in path.parent.iterdir()) == ["release-floor.json"]
