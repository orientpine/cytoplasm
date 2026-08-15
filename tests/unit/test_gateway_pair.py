from __future__ import annotations

from dataclasses import dataclass, field

from automation.gateway_pair import (
    Command,
    Gateway,
    GatewayAction,
    GatewayEffects,
    GatewayPair,
    run_pair,
)


@dataclass(slots=True)  # noqa: MUTABLE_OK - the fake records effect calls
class _FakeGatewayEffects:
    failed_account: str | None = None
    calls: list[Command] = field(default_factory=list)

    def uid_for(self, account: str) -> int:
        return {"agent-test": 1001, "peer-test": 1002}[account]

    def run(self, command: Command) -> int:
        self.calls.append(command)
        return 1 if self.failed_account is not None and self.failed_account in command else 0

    def effects(self) -> GatewayEffects:
        return GatewayEffects(uid_for=self.uid_for, run=self.run)


def _pair() -> GatewayPair:
    return GatewayPair(
        agent=Gateway(account="agent-test", unit="agent-gateway.service"),
        peer=Gateway(account="peer-test", unit="peer-gateway.service"),
    )


def test_restart_attempts_both_gateways_when_the_first_account_fails() -> None:
    # Given
    fake = _FakeGatewayEffects(failed_account="agent-test")

    # When
    result = run_pair(_pair(), GatewayAction.RESTART, fake.effects())

    # Then
    assert result == 1
    assert len(fake.calls) == 2
    assert "agent-test" in fake.calls[0]
    assert "peer-test" in fake.calls[1]
    assert all(command[-2:] == ("restart", command[-1]) for command in fake.calls)


def test_health_checks_both_configured_gateway_units() -> None:
    # Given
    fake = _FakeGatewayEffects()

    # When
    result = run_pair(_pair(), GatewayAction.HEALTH, fake.effects())

    # Then
    assert result == 0
    assert [command[-1] for command in fake.calls] == [
        "agent-gateway.service",
        "peer-gateway.service",
    ]
    assert all(command[-2] == "is-active" for command in fake.calls)
