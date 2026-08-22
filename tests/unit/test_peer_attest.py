from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from automation import peer_attest
from automation.skill_review import skill_digest


_NONCE = "1" * 32
_OWNER_ID = "111111111111111111"
_APPROVALS_CHANNEL_ID = "100000000000000009"


@dataclass
class FakeDiscordTransport:
    channel_id: str = "channel-1"
    replies: list[tuple[str, str, str]] = field(default_factory=list)
    existing: list[dict[str, object]] = field(default_factory=list)

    def replies_after(self, channel_id: str, message_id: str) -> list[dict[str, object]]:
        assert channel_id == self.channel_id
        assert message_id
        return self.existing

    def post_reply(self, channel_id: str, message_id: str, content: str) -> None:
        self.replies.append((channel_id, message_id, content))


def _skill(tmp_path: Path, scenario: str = "echo SCENARIO-PASS") -> Path:
    skill_dir = tmp_path / "calendar"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    _ = (skill_dir / "SKILL.md").write_text(
        "---\nname: calendar\ndescription: Deterministic calendar skill.\n---\n",
        encoding="utf-8",
    )
    scenario_path = scripts / "scenario.sh"
    _ = scenario_path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{scenario}\n", encoding="utf-8")
    scenario_path.chmod(0o700)
    return skill_dir


def _request(skill_dir: Path, digest: str) -> peer_attest.AttestRequest:
    return peer_attest.AttestRequest(
        skill="calendar",
        staged_dir=skill_dir,
        expected_digest=digest,
        request_message_id="request-1",
        deploy_nonce=_NONCE,
        channel_id="channel-1",
    )


def test_attest_when_peer_sandbox_passes_then_posts_exact_bound_pass(tmp_path: Path) -> None:
    # Given: a peer-owned staged skill passes all four deterministic checks.
    skill_dir = _skill(tmp_path)
    transport = FakeDiscordTransport()

    # When: the peer attestor independently reviews its sandbox bytes.
    result = peer_attest.attest(_request(skill_dir, skill_digest(skill_dir)), transport)

    # Then: it replies with the sole canonical PASS attestation body.
    assert result.exit_code == 0
    assert transport.replies == [
        (
            "channel-1",
            "request-1",
            "[skill-attest] request="
            f"{_NONCE} skill=calendar sha256={skill_digest(skill_dir)} "
            "verdict=PASS reviewer=peer-sandbox-v1",
        )
    ]


def test_attest_when_deterministic_check_fails_then_posts_non_pass_and_blocks(tmp_path: Path) -> None:
    # Given: the peer sandbox scenario does not emit the required success marker.
    skill_dir = _skill(tmp_path, "echo scenario failed")
    transport = FakeDiscordTransport()

    # When: the peer attestor runs the four deterministic checks.
    result = peer_attest.attest(_request(skill_dir, skill_digest(skill_dir)), transport)

    # Then: it reports FAIL and never returns an approvable result.
    assert result.exit_code == 1
    assert len(transport.replies) == 1
    assert "verdict=FAIL" in transport.replies[0][2]
    assert "verdict=PASS" not in transport.replies[0][2]


def test_attest_when_expected_digest_differs_then_posts_fail_and_blocks(tmp_path: Path) -> None:
    # Given: the peer sandbox bytes differ from the digest bound to the request.
    skill_dir = _skill(tmp_path)
    transport = FakeDiscordTransport()

    # When: the peer recomputes its own sandbox digest.
    result = peer_attest.attest(_request(skill_dir, "b" * 64), transport)

    # Then: the mismatch is a published FAIL, not a forged PASS for the request hash.
    assert result.exit_code == 1
    assert transport.replies[0][2] == (
        "[skill-attest] request="
        f"{_NONCE} skill=calendar sha256={skill_digest(skill_dir)} "
        "verdict=FAIL reviewer=peer-sandbox-v1"
    )


def test_attest_when_bound_reply_already_present_then_skips_duplicate_post(tmp_path: Path) -> None:
    # Given: this peer already replied to the request with the bound attestation body.
    skill_dir = _skill(tmp_path)
    digest = skill_digest(skill_dir)
    prior: dict[str, object] = {
        "message_reference": {"message_id": "request-1", "channel_id": "channel-1"},
        "content": (
            "[skill-attest] request="
            f"{_NONCE} skill=calendar sha256={digest} verdict=PASS reviewer=peer-sandbox-v1"
        ),
    }
    transport = FakeDiscordTransport(existing=[prior])

    # When: the peer attestor runs again for the same nonce, skill, and digest.
    result = peer_attest.attest(_request(skill_dir, digest), transport)

    # Then: the verdict still stands but no second reply is posted to the channel.
    assert result.exit_code == 0
    assert transport.replies == []


def test_attest_when_prior_reply_binds_a_different_nonce_then_posts_fresh_attestation(tmp_path: Path) -> None:
    # Given: a bound reply exists, but for a different deploy nonce (not this request).
    skill_dir = _skill(tmp_path)
    digest = skill_digest(skill_dir)
    stale: dict[str, object] = {
        "message_reference": {"message_id": "request-1", "channel_id": "channel-1"},
        "content": (
            "[skill-attest] request="
            f"{'2' * 32} skill=calendar sha256={digest} verdict=PASS reviewer=peer-sandbox-v1"
        ),
    }
    transport = FakeDiscordTransport(existing=[stale])

    # When: the peer attestor runs for this request's nonce.
    result = peer_attest.attest(_request(skill_dir, digest), transport)

    # Then: the mismatched prior reply does not suppress this request's attestation.
    assert result.exit_code == 0
    assert len(transport.replies) == 1


def _tmp_checkout(tmp_path: Path) -> Path:
    """Copy the stdlib-only attestor module set into a standalone checkout."""
    repo = tmp_path / "repo"
    (repo / "automation").mkdir(parents=True)
    src = Path(peer_attest.__file__).parent
    for name in (
        "__init__.py",
        "git_tag_signature.py",
        "peer_attest.py",
        "peer_attest_runtime.py",
        "peer_attestation.py",
        "peer_signed_attestation.py",
        "scenario_runner.py",
        "skill_review.py",
    ):
        _ = shutil.copy(src / name, repo / "automation" / name)
        (repo / "automation" / name).chmod(0o644)
    repo.chmod(0o755)
    (repo / "automation").chmod(0o755)
    return repo


_ATTEST_ARGS = [
    "--skill",
    "calendar",
    "--staged-dir",
    "/nonexistent",
    "--hash",
    "a" * 64,
    "--request-message-id",
    "1",
    "--deploy-nonce",
    "1" * 32,
]


def test_find_tamperable_path_when_checkout_clean_then_returns_none(tmp_path: Path) -> None:
    # Given: a standalone checkout whose root and verifier files all have clean permissions.
    repo = _tmp_checkout(tmp_path)

    # When: the writability guard scans the checkout.
    tamperable = peer_attest._find_tamperable_path(repo)

    # Then: no path is reported as tamperable.
    assert tamperable is None


def test_find_tamperable_path_when_repo_root_group_writable_then_returns_root(tmp_path: Path) -> None:
    # Given: the checkout root itself is group-writable.
    repo = _tmp_checkout(tmp_path)
    repo.chmod(0o775)

    # When: the writability guard scans the checkout.
    tamperable = peer_attest._find_tamperable_path(repo)

    # Then: the checkout root is reported as the tamperable path.
    assert tamperable == repo


def test_find_tamperable_path_when_verifier_file_other_writable_then_returns_file(tmp_path: Path) -> None:
    # Given: one verifier module is other-writable inside an otherwise clean checkout.
    repo = _tmp_checkout(tmp_path)
    writable = repo / "automation" / "peer_attestation.py"
    writable.chmod(0o646)

    # When: the writability guard scans the checkout.
    tamperable = peer_attest._find_tamperable_path(repo)

    # Then: that exact verifier file is reported as tamperable.
    assert tamperable == writable


def test_find_tamperable_path_when_verifier_file_missing_then_fails_closed(tmp_path: Path) -> None:
    # Given: one verifier module is missing from the checkout entirely.
    repo = _tmp_checkout(tmp_path)
    missing = repo / "automation" / "skill_review.py"
    missing.unlink()

    # When: the writability guard scans the checkout.
    tamperable = peer_attest._find_tamperable_path(repo)

    # Then: the missing verifier is reported (missing = fail-closed).
    assert tamperable == missing


def test_main_when_checkout_group_writable_then_exits_2_with_writability_fatal(tmp_path: Path) -> None:
    # Given: a standalone checkout whose root is group-writable.
    repo = _tmp_checkout(tmp_path)
    repo.chmod(0o775)

    # When: the attestor CLI runs from that checkout.
    completed = subprocess.run(
        [sys.executable, str(repo / "automation" / "peer_attest.py"), *_ATTEST_ARGS],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: it refuses with exit 2 and the writability FATAL before any other guard.
    assert completed.returncode == 2
    assert "group/other-writable" in completed.stderr


def test_main_when_checkout_clean_but_not_srv_then_exits_2_with_srv_fatal(tmp_path: Path) -> None:
    # Given: a standalone checkout with clean permissions, outside /srv/autophagy-agents.
    repo = _tmp_checkout(tmp_path)

    # When: the attestor CLI runs from that checkout.
    completed = subprocess.run(
        [sys.executable, str(repo / "automation" / "peer_attest.py"), *_ATTEST_ARGS],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the location guard fires (a standalone tmp checkout is neither the
    # mirror nor a release runtime), not the writability guard.
    assert completed.returncode == 2
    assert "must run from the release runtime or /srv/autophagy-agents" in completed.stderr
    assert "group/other-writable" not in completed.stderr


def test_attest_prefers_request_channel_id_over_transport_scan(tmp_path: Path) -> None:
    # Given: a transport that can only read and reply — resolving is not in its Protocol.
    skill_dir = _skill(tmp_path)
    assert not hasattr(FakeDiscordTransport, "approvals_channel_id")

    transport = FakeDiscordTransport(channel_id="chan-x")
    request = peer_attest.AttestRequest(
        skill="calendar",
        staged_dir=skill_dir,
        expected_digest=skill_digest(skill_dir),
        request_message_id="request-1",
        deploy_nonce=_NONCE,
        channel_id="chan-x",
    )

    # When: the peer attestor runs with an explicit request channel id.
    result = peer_attest.attest(request, transport)

    # Then: the reply lands on the channel main() already bound; attest resolves nothing.
    assert result.exit_code == 0
    assert transport.replies[0][0] == "chan-x"


def test_parse_request_maps_channel_id_flag() -> None:
    # Given: valid attestation argv, varied only by the --channel-id flag.
    # When: the CLI parser maps argv into an AttestRequest.
    with_flag = peer_attest._parse_request([*_ATTEST_ARGS, "--channel-id", "abc"])
    without_flag = peer_attest._parse_request(list(_ATTEST_ARGS))
    empty_flag = peer_attest._parse_request([*_ATTEST_ARGS, "--channel-id", ""])

    # Then: an explicit id maps through; absent and empty stay empty for main() to bind.
    assert with_flag is not None and with_flag.channel_id == "abc"
    assert without_flag is not None and without_flag.channel_id == ""
    assert empty_flag is not None and empty_flag.channel_id == ""


def _guild_scan_api(guilds: list[dict[str, str]], channels: dict[str, list[dict[str, Any]]]):
    def _api(self: peer_attest.DiscordRestTransport, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        assert method == "GET"
        assert payload is None
        if path == "/users/@me/guilds":
            return guilds
        if path.startswith("/channels/"):
            return {"type": 0, "name": "approvals", "id": path.removeprefix("/channels/")}
        for guild in guilds:
            if path == f"/guilds/{guild['id']}/channels":
                return channels[guild["id"]]
        raise AssertionError(f"unexpected API path: {path}")

    return _api


def _attest_surface_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the peer's directory at fixture paths — no home config, no real Discord."""
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setattr(peer_attest, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(peer_attest, "GATE_DIR", tmp_path / "skill-gate")


def _resolved_attest_channel() -> str:
    return peer_attest._attest_channel_id(peer_attest.DiscordRestTransport("dummy"))


def _refusal_chain(error: BaseException) -> str:
    """Every message on the raised chain — the directory reports WHY one level down."""
    causes: list[str] = []
    current: BaseException | None = error
    while current is not None:
        causes.append(str(current))
        current = current.__cause__
    return " | ".join(causes)


def test_attest_channel_guild_scan_multi_match_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: two guilds each expose a text channel named approvals.
    api = _guild_scan_api(
        [{"id": "g1"}, {"id": "g2"}],
        {
            "g1": [{"id": "111", "type": 0, "name": "approvals"}],
            "g2": [{"id": "222", "type": 0, "name": "approvals"}],
        },
    )
    monkeypatch.setattr(peer_attest.DiscordRestTransport, "api", api)
    _attest_surface_env(tmp_path, monkeypatch)

    # When / Then: the ambiguous scan fails closed instead of picking the first guild.
    with pytest.raises(OSError, match="surface unresolved") as excinfo:
        _ = _resolved_attest_channel()
    assert "absent or ambiguous across guilds" in _refusal_chain(excinfo.value)


def test_attest_channel_guild_scan_single_match_returns_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: exactly one guild exposes a text channel named approvals.
    api = _guild_scan_api(
        [{"id": "g1"}, {"id": "g2"}],
        {
            "g1": [
                {"id": "333", "type": 2, "name": "approvals"},
                {"id": _APPROVALS_CHANNEL_ID, "type": 0, "name": "approvals"},
            ],
            "g2": [{"id": "444", "type": 0, "name": "general"}],
        },
    )
    monkeypatch.setattr(peer_attest.DiscordRestTransport, "api", api)
    _attest_surface_env(tmp_path, monkeypatch)

    # When: the peer resolves its declared SKILL_ATTEST surface through the shared directory.
    channel_id = _resolved_attest_channel()

    # Then: the single verified text-channel match is returned.
    assert channel_id == _APPROVALS_CHANNEL_ID


def test_attest_channel_guild_scan_no_match_raises_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: no guild exposes a text channel named approvals.
    api = _guild_scan_api([{"id": "g1"}], {"g1": [{"id": "444", "type": 0, "name": "general"}]})
    monkeypatch.setattr(peer_attest.DiscordRestTransport, "api", api)
    _attest_surface_env(tmp_path, monkeypatch)

    # When / Then: an absent channel refuses too — the peer never invents a surface.
    with pytest.raises(OSError, match="surface unresolved") as excinfo:
        _ = _resolved_attest_channel()
    assert "absent or ambiguous across guilds" in _refusal_chain(excinfo.value)


def test_peer_attest_defines_no_resolver_of_its_own() -> None:
    # Given / When: the module source after AS-1.10.
    source = Path(peer_attest.__file__).read_text(encoding="utf-8")

    # Then: neither the Protocol nor the REST transport declares a channel resolver (SI-2),
    # and the guild scan they used to own now lives only in the shared directory.
    assert "approvals_channel_id" not in source
    assert "/users/@me/guilds" not in source


# --- DG-4 W3.A: attestor may run from the immutable release root, not only the mirror ---

def test_is_trusted_attestor_root_accepts_the_ops_mirror(tmp_path: Path) -> None:  # noqa: ARG001
    # The resident /srv/autophagy-agents checkout stays a trusted attestor root.
    assert peer_attest._is_trusted_attestor_root(peer_attest.OPS_REPO_ROOT)


def test_is_trusted_attestor_root_accepts_a_release_child(tmp_path: Path) -> None:
    # A direct child of the releases root (a by-value installed release) is trusted.
    releases = tmp_path / "releases"
    release = releases / ("a" * 40)
    release.mkdir(parents=True)
    assert peer_attest._is_trusted_attestor_root(release, releases_root=releases)


def test_is_trusted_attestor_root_accepts_realpath_of_current(tmp_path: Path) -> None:
    # REPO_ROOT resolves the `current` symlink to <releases>/<sha>; that must be trusted.
    releases = tmp_path / "releases"
    release = releases / ("b" * 40)
    release.mkdir(parents=True)
    current = tmp_path / "current"
    current.symlink_to(release, target_is_directory=True)
    assert peer_attest._is_trusted_attestor_root(
        current.resolve(), releases_root=releases, release_current=current
    )


def test_is_trusted_attestor_root_refuses_an_arbitrary_path(tmp_path: Path) -> None:
    # Anything that is neither the mirror, a release child, nor current is refused.
    releases = tmp_path / "releases"
    releases.mkdir()
    stranger = tmp_path / "somewhere-else"
    stranger.mkdir()
    assert not peer_attest._is_trusted_attestor_root(stranger, releases_root=releases)


def test_is_trusted_attestor_root_refuses_the_releases_root_itself(tmp_path: Path) -> None:
    # The releases parent is not a release; only its direct children are.
    releases = tmp_path / "releases"
    releases.mkdir()
    assert not peer_attest._is_trusted_attestor_root(releases, releases_root=releases)


def test_is_trusted_attestor_root_refuses_a_nested_grandchild(tmp_path: Path) -> None:
    # Only DIRECT children of releases are trusted, not deeper paths.
    releases = tmp_path / "releases"
    nested = releases / ("c" * 40) / "automation"
    nested.mkdir(parents=True)
    assert not peer_attest._is_trusted_attestor_root(nested, releases_root=releases)
