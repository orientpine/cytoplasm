"""Offline S2 legacy-drain fixture probe; never targets production state."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias
from urllib.parse import quote

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from automation.interop.approval_lifecycle import (  # noqa: E402 - direct execution adds the repo root above.
    ApprovalRequest,
    Probe,
    WatchOutcome,
    resolve_owner_decision,
)
from automation.interop.approval_surface import (  # noqa: E402 - direct execution adds the repo root above.
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ApprovalSurfaceError,
    ChannelFacts,
    validate_stored_binding,
)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
Call: TypeAlias = tuple[str, str]

_GUILD = "1528936606856122421"
_OWNER_DM = "1526487935975952385"
_OWNER = "280680578314010625"
_SEED_PATH = Path("public/as-s2.json")
_SEED: dict[str, JsonValue] = {
    "id": "as-s2",
    "kind": "mail_reply",
    "surface": "skill-approvals",
    "channel_id": _GUILD,
    "policy_version": 1,
    "status": "pending",
    "sha256": "as-s2-fixture-hash",
    "message_id": "999000999000999000",
    "uid": "as-s2-uid",
    "created": "2026-07-26T00:00:00Z",
}


class DriverError(RuntimeError):
    pass


class Api(Protocol):
    def __call__(self, method: str, path: str, payload: dict[str, JsonValue] | None = None) -> JsonValue: ...


@dataclass(frozen=True, slots=True)
class FixtureDirectory:
    def owner_dm(self) -> str:
        return _OWNER_DM

    def skill_approvals(self) -> str:
        return _GUILD

    def describe(self, channel_id: str) -> ChannelFacts:
        if channel_id == _GUILD:
            return ChannelFacts(0, "approvals", ())
        if channel_id == _OWNER_DM:
            return ChannelFacts(1, "", (_OWNER,))
        raise ApprovalSurfaceError("fixture directory received an unknown channel")


class FixtureApi:
    """Mutable fake API that retains the observable request sequence."""

    def __init__(self, action_hash: str) -> None:
        self.action_hash = action_hash
        self.calls: list[Call] = []

    def __call__(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        del payload
        self.calls.append((method, path))
        message_path = f"/channels/{_GUILD}/messages/{_SEED['message_id']}"
        reaction_path = f"{message_path}/reactions/{quote('✅', safe='')}?limit=100"
        if method == "GET" and path == message_path:
            return {"content": self.action_hash}
        if method == "GET" and path == reaction_path:
            return [{"id": _OWNER, "bot": False}]
        raise DriverError(f"unexpected fixture API call: {method} {path}")


class FixtureWatcher:
    """Mutable watcher whose apply marker prevents a pre-consumption drop."""

    def __init__(self, path: Path, action_hash: str, api: Api) -> None:
        self.path = path
        self.action_hash = action_hash
        self.api = api
        self.applied = False

    def probe(self, request: ApprovalRequest) -> Probe:
        message = self.api("GET", f"/channels/{request.channel_id}/messages/{request.message_id}")
        if not isinstance(message, dict):
            raise DriverError("fixture message response is malformed")
        content = message.get("content")
        if not isinstance(content, str) or request.action_hash not in content:
            return Probe.BINDING_MISMATCH
        users = self.api(
            "GET",
            f"/channels/{request.channel_id}/messages/{request.message_id}/reactions/{quote('✅', safe='')}?limit=100",
        )
        if not isinstance(users, list):
            raise DriverError("fixture reaction response is malformed")
        approved = any(
            isinstance(user, dict) and user.get("id") == _OWNER and user.get("bot") is False
            for user in users
        )
        return Probe.APPROVED if approved else Probe.BOUND_PENDING

    def apply(self, request: ApprovalRequest, decision: Probe) -> None:
        del request
        if decision is not Probe.APPROVED:
            raise DriverError("fixture owner did not approve")
        self.applied = True

    def drop(self, request: ApprovalRequest) -> None:
        del request
        if not self.applied:
            raise DriverError("fixture record would be dropped before consumption")
        record = _load(self.path)
        record["status"] = "consumed"
        self.path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class FixtureLease:
    @contextmanager
    def hold(self, key: str) -> Iterator[bool]:
        del key
        yield True


def _fixture_path(fixture: Path) -> Path:
    return fixture / _SEED_PATH


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _refuse_production_fixture(fixture: Path) -> None:
    resolved = fixture.resolve(strict=False)
    protected = (Path.home() / ".hermes", Path("/srv/autophagy-private"))
    if any(_is_below(resolved, root.resolve(strict=False)) for root in protected):
        raise DriverError("refuses production fixture root")


def _load(path: Path) -> dict[str, JsonValue]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DriverError("fixture seed is unreadable") from error
    if not isinstance(raw, dict):
        raise DriverError("fixture seed is not an object")
    return raw


def _required(record: dict[str, JsonValue], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise DriverError(f"fixture seed omitted {key}")
    return value


def _binding(record: dict[str, JsonValue]) -> ApprovalBinding:
    version = record.get("policy_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise DriverError("fixture seed has invalid policy version")
    if _required(record, "kind") != "mail_reply":
        raise DriverError("fixture seed has invalid kind")
    try:
        surface = ApprovalSurface(_required(record, "surface"))
    except ValueError as error:
        raise DriverError("fixture seed has invalid surface") from error
    binding = ApprovalBinding(ApprovalKind.MAIL_REPLY, surface, _required(record, "channel_id"), version)
    return validate_stored_binding(binding, FixtureDirectory(), _OWNER)


def seed(fixture: Path) -> None:
    path = _fixture_path(fixture)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_SEED, sort_keys=True), encoding="utf-8")


def consume(fixture: Path) -> list[Call]:
    path = _fixture_path(fixture)
    record = _load(path)
    binding = _binding(record)
    if binding.surface is not ApprovalSurface.SKILL_APPROVALS or binding.channel_id != _GUILD:
        raise DriverError("legacy binding did not retain the guild approval surface")
    request = ApprovalRequest(
        key=f"mail:reply:{_required(record, 'uid')}",
        action_hash=_required(record, "sha256"),
        message_id=_required(record, "message_id"),
        channel_id=binding.channel_id,
        created_at=_required(record, "created"),
    )
    api = FixtureApi(request.action_hash)
    verdict = resolve_owner_decision(request, FixtureWatcher(path, request.action_hash, api), FixtureLease())
    if verdict.outcome is not WatchOutcome.CONSUMED:
        raise DriverError(f"legacy record did not consume: {verdict.outcome}")
    if any(_OWNER_DM in endpoint for _method, endpoint in api.calls):
        raise DriverError("legacy drain retargeted the guild message to the owner DM")
    return api.calls


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--seed-only", action="store_true")
    action.add_argument("--consume", action="store_true")
    args = parser.parse_args(argv)
    try:
        _refuse_production_fixture(args.fixture)
        if args.seed_only:
            seed(args.fixture)
            return 0
        calls = consume(args.fixture)
    except (ApprovalSurfaceError, DriverError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS")
    print(f"calls={calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
