from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from automation.managed_skills.announce import (
    AnnounceOutcome,
    announce_release,
    resolve_announce_channel_id,
)
from automation.managed_skills.announce_ledger import AnnounceLedger
from automation.managed_skills.manifest import ManagedManifest

_VALID_PUBLIC_KEY = " ".join(
    (
        "ssh-ed25519",
        "AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g",
        "roster-example-admin",
    )
)


@dataclasses.dataclass(frozen=True)
class FakeSent:
    message_id: str


@dataclasses.dataclass
class FakeTransport:
    channel_id: str
    sent: list[str] = dataclasses.field(default_factory=list)

    def send(self, body: str) -> tuple[FakeSent, ...]:
        self.sent.append(body)
        return (FakeSent(message_id=f"m{len(self.sent)}"),)


def _manifest(*, breaking: bool = False, changelog: str = "Capability delta.") -> ManagedManifest:
    return ManagedManifest(
        schema_version=1,
        publisher="cha",
        skill="managed-x",
        release_sequence=7,
        source_commit=None,
        skill_sha256="a" * 64,
        previous_sha256=None,
        compatibility="any",
        breaking=breaking,
        revoked_digests=(),
        changelog=changelog,
        migration="run scripts/migrate.sh" if breaking else None,
    )


def _roster_yaml(*, announce_channel_id: str | None = None) -> str:
    lines = [
        "schema: 1",
        "group_id: example-lab",
        "admin:",
        "  name: Example Admin",
        '  discord_user_id: "1001"',
        "  publisher_principal: publisher-example-admin@autophagy",
        f"  signing_public_key: {_VALID_PUBLIC_KEY}",
        "members: []",
    ]
    if announce_channel_id is not None:
        lines.append(f'announce_channel_id: "{announce_channel_id}"')
    return "\n".join(lines) + "\n"


def test_announce_release_when_channel_present_then_sends_one_notification(
    tmp_path: Path,
) -> None:
    # Given: a validated manifest and a fake transport bound to the announce channel.
    manifest = _manifest()
    transport = FakeTransport(channel_id="123")

    # When: the announce post is rendered.
    announce_release(
        manifest,
        "managed-x/v7",
        transport=transport,
        channel_id="123",
        ledger=AnnounceLedger(root=tmp_path),
    )

    # Then: one notification is sent with the required release facts.
    assert len(transport.sent) == 1
    body = transport.sent[0]
    assert "managed-x" in body
    assert "managed-x/v7" in body
    assert "aaaaaaaaaaaa" in body
    assert "breaking=false" in body
    assert "publisher-node canary 진행 중" in body


@pytest.mark.parametrize("channel_id", [None, ""])
def test_announce_release_when_channel_is_missing_then_noops(
    channel_id: str | None, tmp_path: Path
) -> None:
    # Given: no announce channel and a fake transport.
    manifest = _manifest()
    transport = FakeTransport(channel_id="123")

    # When: announcement is attempted.
    announce_release(
        manifest,
        "managed-x/v7",
        transport=transport,
        channel_id=channel_id,
        ledger=AnnounceLedger(root=tmp_path),
    )

    # Then: it returns normally without sending anything.
    assert transport.sent == []


def test_announce_release_when_rendered_then_omits_activation_and_reaction_prompts(
    tmp_path: Path,
) -> None:
    # Given: a normal release manifest.
    manifest = _manifest()
    transport = FakeTransport(channel_id="123")

    # When: the announce body is produced.
    announce_release(
        manifest,
        "managed-x/v7",
        transport=transport,
        channel_id="123",
        ledger=AnnounceLedger(root=tmp_path),
    )

    # Then: it contains no activation or reaction call-to-action.
    body = transport.sent[0]
    assert "\u2705" not in body
    assert "활성화" not in body
    assert "리액션" not in body


def test_announce_release_when_changelog_contains_urls_then_redacts_repo_and_key_material(
    tmp_path: Path,
) -> None:
    # Given: a changelog that would leak repository or token-shaped material if echoed verbatim.
    manifest = _manifest(
        changelog="See https://example.com/repo.git and git@github.com:org/repo.git; token sk-abc ghp_def.",
    )
    transport = FakeTransport(channel_id="123")

    # When: the announce body is rendered.
    announce_release(
        manifest,
        "managed-x/v7",
        transport=transport,
        channel_id="123",
        ledger=AnnounceLedger(root=tmp_path),
    )

    # Then: the message omits repository URL fragments and key-shaped strings.
    body = transport.sent[0]
    assert "http" not in body
    assert "git@" not in body
    assert ".git" not in body
    assert "sk-" not in body
    assert "ghp_" not in body


@pytest.mark.parametrize("breaking, marker_present", [(True, True), (False, False)])
def test_announce_release_when_breaking_changes_then_marks_only_breaking_releases(
    breaking: bool, marker_present: bool, tmp_path: Path
) -> None:
    # Given: a breaking or non-breaking manifest.
    manifest = _manifest(breaking=breaking)
    transport = FakeTransport(channel_id="123")

    # When: the message is rendered.
    announce_release(
        manifest,
        "managed-x/v7",
        transport=transport,
        channel_id="123",
        ledger=AnnounceLedger(root=tmp_path),
    )

    # Then: only breaking releases carry a visible breaking marker.
    body = transport.sent[0]
    assert ("⚠ BREAKING" in body) is marker_present


def test_announce_release_when_same_release_published_twice_then_posts_once(
    tmp_path: Path,
) -> None:
    # Given: one release and a ledger that survives between publish runs.
    manifest = _manifest()
    transport = FakeTransport(channel_id="123")
    ledger = AnnounceLedger(root=tmp_path)

    # When: publish announces the identical release twice.
    first = announce_release(
        manifest, "managed-x/v7", transport=transport, channel_id="123", ledger=ledger
    )
    second = announce_release(
        manifest, "managed-x/v7", transport=transport, channel_id="123", ledger=ledger
    )

    # Then: exactly one message exists and the stored id is reported, never replaced.
    assert len(transport.sent) == 1
    assert first.sent is True
    assert first.outcome is AnnounceOutcome.POSTED
    assert second.sent is False
    assert second.outcome is AnnounceOutcome.ALREADY_ANNOUNCED
    assert second.message_id == first.message_id


def test_announce_release_when_different_releases_then_each_is_announced(
    tmp_path: Path,
) -> None:
    # Given: two distinct releases of the same skill sharing one ledger.
    transport = FakeTransport(channel_id="123")
    ledger = AnnounceLedger(root=tmp_path)

    # When: both are announced.
    for sequence in (7, 8):
        manifest = dataclasses.replace(_manifest(), release_sequence=sequence)
        _ = announce_release(
            manifest,
            f"managed-x/v{sequence}",
            transport=transport,
            channel_id="123",
            ledger=ledger,
        )

    # Then: dedup is per release, not per skill.
    assert len(transport.sent) == 2


def test_announce_release_when_binding_changed_then_refuses_instead_of_reposting(
    tmp_path: Path,
) -> None:
    # Given: a release already announced into one channel.
    manifest = _manifest()
    transport = FakeTransport(channel_id="123")
    ledger = AnnounceLedger(root=tmp_path)
    _ = announce_release(
        manifest, "managed-x/v7", transport=transport, channel_id="123", ledger=ledger
    )

    # When: the same tag is announced with a different binding (another channel).
    verdict = announce_release(
        manifest, "managed-x/v7", transport=transport, channel_id="456", ledger=ledger
    )

    # Then: the mismatch is refused, not silently double-posted.
    assert verdict.sent is False
    assert verdict.outcome is AnnounceOutcome.BINDING_MISMATCH
    assert len(transport.sent) == 1


def test_resolve_announce_channel_when_roster_declares_one_then_it_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a roster whose group announces into its own channel.
    roster = tmp_path / "roster.yaml"
    _ = roster.write_text(_roster_yaml(announce_channel_id="1004"), encoding="utf-8")
    monkeypatch.setenv("AUTOPHAGY_ROSTER", str(roster))
    monkeypatch.setenv("MANAGED_ANNOUNCE_CHANNEL_ID", "999")

    # Then: the roster target is used, not the environment one.
    assert resolve_announce_channel_id() == "1004"


def test_resolve_announce_channel_when_roster_omits_it_then_environment_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid roster that declares no announce channel.
    roster = tmp_path / "roster.yaml"
    _ = roster.write_text(_roster_yaml(), encoding="utf-8")
    monkeypatch.setenv("AUTOPHAGY_ROSTER", str(roster))
    monkeypatch.setenv("MANAGED_ANNOUNCE_CHANNEL_ID", "999")

    # Then: the pre-existing environment behaviour is preserved exactly.
    assert resolve_announce_channel_id() == "999"


def test_resolve_announce_channel_when_roster_is_absent_then_environment_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: no roster at all (the pre-this-task installation shape).
    monkeypatch.setenv("AUTOPHAGY_ROSTER", str(tmp_path / "absent.yaml"))
    monkeypatch.setenv("MANAGED_ANNOUNCE_CHANNEL_ID", "999")

    # Then: announcing still follows the environment.
    assert resolve_announce_channel_id() == "999"
