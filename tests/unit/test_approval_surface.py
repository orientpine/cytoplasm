"""Tests for the versioned owner-approval surface policy."""
from __future__ import annotations

import pytest

from automation.interop.approval_surface import (
    POLICY_VERSION,
    _TRANSITIONS,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ApprovalSurfaceError,
    ChannelFacts,
    TransitionLedger,
    legacy_binding,
    reaction_instruction,
    required_surface,
    resolve_new_binding,
    surface_at_policy,
    validate_stored_binding,
)

OWNER_ID = "280680578314010625"
DM_CHANNEL_ID = "1526487935975952385"
SKILL_CHANNEL_ID = "1528936606856122421"

class FakeDirectory:
    """Mutable in-memory directory used to exercise the injected boundary."""

    def __init__(self) -> None:
        self.dm_channel_id: str = DM_CHANNEL_ID
        self.skill_channel_id: str = SKILL_CHANNEL_ID
        self.facts: dict[str, ChannelFacts] = {
            DM_CHANNEL_ID: ChannelFacts(1, "", (OWNER_ID,)),
            SKILL_CHANNEL_ID: ChannelFacts(0, "approvals", ()),
        }
        self.calls: list[str] = []

    def owner_dm(self) -> str:
        self.calls.append("owner_dm")
        return self.dm_channel_id

    def skill_approvals(self) -> str:
        self.calls.append("skill_approvals")
        return self.skill_channel_id

    def describe(self, channel_id: str) -> ChannelFacts:
        self.calls.append(f"describe:{channel_id}")
        return self.facts[channel_id]


class RaisingDirectory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def owner_dm(self) -> str:
        self.calls.append("owner_dm")
        raise ApprovalSurfaceError("directory unavailable")

    def skill_approvals(self) -> str:
        self.calls.append("skill_approvals")
        return SKILL_CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        self.calls.append(f"describe:{channel_id}")
        return ChannelFacts(0, "approvals", ())


def _mail_flipped_at_v2() -> TransitionLedger:
    return {
        **_TRANSITIONS,
        ApprovalKind.MAIL_REPLY: (
            *_TRANSITIONS[ApprovalKind.MAIL_REPLY],
            (2, ApprovalSurface.OWNER_DM),
        ),
    }


def test_approval_kind_has_exactly_fourteen_members() -> None:
    # Given / When: the closed set of approval kinds is enumerated.
    kinds = tuple(ApprovalKind)

    # Then: every planned flow has exactly one kind.
    assert len(kinds) == 14


@pytest.mark.parametrize(
    "kind",
    (
        ApprovalKind.MAIL_COMPOSE,
        ApprovalKind.CALENDAR,
        ApprovalKind.COORDINATION,
        ApprovalKind.WIKI,
        ApprovalKind.OBSIDIAN_WRITE,
    ),
)
def test_already_dm_kinds_land_on_owner_dm(kind: ApprovalKind) -> None:
    # Given / When: an already-DM flow is resolved at the landing version.
    surface = surface_at_policy(kind, 0)

    # Then: its initial transition points to the owner DM.
    assert surface is ApprovalSurface.OWNER_DM


@pytest.mark.parametrize(
    "kind",
    (
        ApprovalKind.MAIL_REPLY,
        ApprovalKind.BUDGET_MAIL,
        ApprovalKind.PATENT_EXPORT,
        ApprovalKind.REPAIR,
    ),
)
def test_migrating_kinds_stay_on_skill_approvals_at_v1(kind: ApprovalKind) -> None:
    # Given / When: a migrating flow is resolved at the R1 baseline.
    surface = surface_at_policy(kind, 1)

    # Then: R1 has not moved it to DM.
    assert surface is ApprovalSurface.SKILL_APPROVALS


@pytest.mark.parametrize(
    "kind",
    (
        ApprovalKind.SKILL_DEPLOY,
        ApprovalKind.SKILL_ATTEST,
        ApprovalKind.SKILL_PUBLISH,
        ApprovalKind.SKILL_SUBMIT,
        ApprovalKind.MANAGED_ACTIVATE,
    ),
)
@pytest.mark.parametrize("policy_version", range(POLICY_VERSION + 6))
def test_supply_chain_never_moves_surfaces(
    kind: ApprovalKind,
    policy_version: int,
) -> None:
    # Given / When: a supply-chain flow is resolved at any foreseeable version.
    surface = surface_at_policy(kind, policy_version)

    # Then: it remains guild-bound and has no transition prepared for a flip.
    assert surface is ApprovalSurface.SKILL_APPROVALS
    assert len(_TRANSITIONS[kind]) == 1


@pytest.mark.parametrize("kind", tuple(ApprovalKind))
def test_each_transition_ledger_starts_at_zero_and_strictly_increases(
    kind: ApprovalKind,
) -> None:
    # Given: one kind's append-only transition tuple.
    versions = tuple(version for version, _surface in _TRANSITIONS[kind])

    # When / Then: its baseline exists and every append advances the version.
    assert versions[0] == 0
    assert all(left < right for left, right in zip(versions, versions[1:], strict=False))


@pytest.mark.parametrize("kind", tuple(ApprovalKind))
def test_required_surface_is_current_policy_projection(kind: ApprovalKind) -> None:
    # Given / When: both new-record and historical policy APIs resolve the current version.
    required = required_surface(kind)
    historical = surface_at_policy(kind, POLICY_VERSION)

    # Then: they are one policy viewed at different times.
    assert required is historical


def test_appending_a_flip_does_not_reinterpret_an_older_version() -> None:
    # Given: mail's transition tuple is copied and a v2 DM flip is appended.
    transitions = _mail_flipped_at_v2()

    # When: the old R1 version is resolved against the extended ledger.
    surface = surface_at_policy(ApprovalKind.MAIL_REPLY, 1, transitions=transitions)

    # Then: the v1 meaning remains guild-bound forever.
    assert surface is ApprovalSurface.SKILL_APPROVALS


def test_stored_binding_survives_a_policy_flip() -> None:
    # Given: a v1 guild binding and a copied ledger whose current v2 policy is DM.
    directory = FakeDirectory()
    binding = ApprovalBinding(
        ApprovalKind.MAIL_REPLY,
        ApprovalSurface.SKILL_APPROVALS,
        SKILL_CHANNEL_ID,
        1,
    )
    transitions = _mail_flipped_at_v2()
    assert surface_at_policy(ApprovalKind.MAIL_REPLY, 2, transitions=transitions) is ApprovalSurface.OWNER_DM

    # When: the stored record is validated using its stamped version.
    validated = validate_stored_binding(
        binding,
        directory,
        OWNER_ID,
        transitions=transitions,
    )

    # Then: it still points to the original guild message.
    assert validated is binding
    assert validated.channel_id == SKILL_CHANNEL_ID


def test_stored_binding_with_contradictory_surface_raises() -> None:
    # Given: a v1 mail record claiming the surface that only starts at v2.
    directory = FakeDirectory()
    binding = ApprovalBinding(
        ApprovalKind.MAIL_REPLY,
        ApprovalSurface.OWNER_DM,
        DM_CHANNEL_ID,
        1,
    )

    # When / Then: validation refuses rather than retargeting the message.
    with pytest.raises(ApprovalSurfaceError):
        _ = validate_stored_binding(binding, directory, OWNER_ID, transitions=_mail_flipped_at_v2())


@pytest.mark.parametrize("channel_id", ("", "dm", "approvals", "abc"))
def test_binding_refuses_an_implicit_or_non_snowflake_channel(channel_id: str) -> None:
    # Given / When / Then: an unresolved channel cannot enter a binding.
    with pytest.raises(ApprovalSurfaceError):
        _ = ApprovalBinding(
            ApprovalKind.MAIL_REPLY,
            ApprovalSurface.SKILL_APPROVALS,
            channel_id,
            POLICY_VERSION,
        )


def test_resolve_new_binding_validates_owner_dm() -> None:
    # Given: an injected directory with a valid owner DM.
    directory = FakeDirectory()

    # When: a DM-routed kind creates a new binding.
    binding = resolve_new_binding(ApprovalKind.MAIL_COMPOSE, directory, OWNER_ID)

    # Then: the concrete DM and current policy version are stamped.
    assert binding == ApprovalBinding(
        ApprovalKind.MAIL_COMPOSE,
        ApprovalSurface.OWNER_DM,
        DM_CHANNEL_ID,
        POLICY_VERSION,
    )


def test_resolve_new_binding_validates_skill_approvals() -> None:
    # Given: an injected directory with a valid approvals channel.
    directory = FakeDirectory()

    # When: a guild-routed kind creates a new binding.
    binding = resolve_new_binding(ApprovalKind.SKILL_ATTEST, directory, OWNER_ID)

    # Then: the concrete guild channel and current policy version are stamped.
    assert binding.channel_id == SKILL_CHANNEL_ID
    assert binding.surface is ApprovalSurface.SKILL_APPROVALS


def test_resolve_new_binding_accepts_an_explicit_skill_channel_id() -> None:
    # Given: a supply-chain caller already has the concrete approvals channel id.
    directory = FakeDirectory()

    # When: the id is supplied explicitly.
    binding = resolve_new_binding(
        ApprovalKind.SKILL_DEPLOY,
        directory,
        OWNER_ID,
        skill_channel_id=SKILL_CHANNEL_ID,
    )

    # Then: it is described and used without another guild-channel lookup.
    assert binding.channel_id == SKILL_CHANNEL_ID
    assert "skill_approvals" not in directory.calls


def test_dm_with_guild_channel_type_raises() -> None:
    # Given: the DM resolver returns facts for a guild text channel.
    directory = FakeDirectory()
    directory.facts[DM_CHANNEL_ID] = ChannelFacts(0, "approvals", (OWNER_ID,))

    # When / Then: the mismatch fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _ = resolve_new_binding(ApprovalKind.MAIL_COMPOSE, directory, OWNER_ID)


def test_dm_without_owner_recipient_raises() -> None:
    # Given: the DM facts omit the owner.
    directory = FakeDirectory()
    directory.facts[DM_CHANNEL_ID] = ChannelFacts(1, "", ("999",))

    # When / Then: the mismatch fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _ = resolve_new_binding(ApprovalKind.MAIL_COMPOSE, directory, OWNER_ID)


def test_skill_channel_with_wrong_name_raises() -> None:
    # Given: the guild channel is named general rather than approvals.
    directory = FakeDirectory()
    directory.facts[SKILL_CHANNEL_ID] = ChannelFacts(0, "general", ())

    # When / Then: the mismatch fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _ = resolve_new_binding(ApprovalKind.SKILL_ATTEST, directory, OWNER_ID)


def test_directory_exception_becomes_surface_error_without_fallback() -> None:
    # Given: owner-DM lookup fails before yielding a channel.
    directory = RaisingDirectory()

    # When / Then: the typed error propagates and no guild fallback is attempted.
    with pytest.raises(ApprovalSurfaceError):
        _ = resolve_new_binding(ApprovalKind.MAIL_COMPOSE, directory, OWNER_ID)
    assert directory.calls == ["owner_dm"]


@pytest.mark.parametrize(
    ("raw_channel_id", "expected_surface"),
    (
        ("", ApprovalSurface.SKILL_APPROVALS),
        (None, ApprovalSurface.SKILL_APPROVALS),
        ("dm", ApprovalSurface.OWNER_DM),
        ("approvals", ApprovalSurface.SKILL_APPROVALS),
    ),
)
def test_legacy_binding_resolves_sentinels_to_concrete_ids(
    raw_channel_id: str | None,
    expected_surface: ApprovalSurface,
) -> None:
    # Given: a legacy mail record with no versioned binding.
    directory = FakeDirectory()

    # When: the named migrator resolves its historical channel shape.
    binding = legacy_binding(ApprovalKind.MAIL_REPLY, raw_channel_id, directory, OWNER_ID)

    # Then: policy v0 and a real snowflake replace the legacy sentinel.
    assert binding.surface is expected_surface
    assert binding.channel_id.isdigit()
    assert binding.policy_version == 0


def test_legacy_binding_keeps_a_concrete_historical_channel() -> None:
    # Given: an already-concrete legacy owner-DM id for a DM-at-v0 flow.
    directory = FakeDirectory()

    # When: the named migrator creates the first-class binding.
    binding = legacy_binding(ApprovalKind.CALENDAR, DM_CHANNEL_ID, directory, OWNER_ID)

    # Then: the concrete id is preserved and validated at policy zero.
    assert binding.channel_id == DM_CHANNEL_ID
    assert binding.surface is ApprovalSurface.OWNER_DM


def test_reaction_instruction_names_no_surface_by_default() -> None:
    # Given / When: an in-message instruction is rendered with the default.
    instruction = reaction_instruction(ApprovalKind.MAIL_REPLY, ApprovalSurface.SKILL_APPROVALS)

    # Then: it is surface-neutral.
    assert instruction == "이 메시지에 ✅ 실행 / ⛔ 취소"


@pytest.mark.parametrize(
    ("surface", "surface_name"),
    (
        (ApprovalSurface.OWNER_DM, "소유자 DM"),
        (ApprovalSurface.SKILL_APPROVALS, "개인 서버 #approvals"),
    ),
)
def test_reaction_instruction_names_surface_when_requested(
    surface: ApprovalSurface,
    surface_name: str,
) -> None:
    # Given / When: an outside hint explicitly asks to name the destination.
    instruction = reaction_instruction(
        ApprovalKind.MAIL_REPLY,
        surface,
        name_surface=True,
    )

    # Then: the central formatter includes the correct human surface name.
    assert surface_name in instruction
