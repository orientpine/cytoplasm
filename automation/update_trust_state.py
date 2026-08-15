"""Durable monotonic release floor for the signed update channel.

C1 (security audit 2026-08-15). ``update_trust`` proves a release tag's
AUTHORSHIP and says nothing about its AGE. An origin compromise that cannot
sign anything can still force-push ``refs/heads/main`` back onto an older
commit whose OWN release tag was genuinely signed long ago and never revoked:
every signature, principal, and TAG-RACE check passes, because the payload IS
authentic. Only its freshness is wrong, so the reconciler "upgrades" prod onto
a known-vulnerable release (classic TUF rollback, CWE-345).

The anchor is the release tag's semantic version, which ``public_export.sh``
already enforces at tag-creation time, persisted outside every git checkout.
That is deliberately the third replication of one rule this project already
relies on — ``managed_sync.state.record_verified`` for managed-skill releases
and ``group_roster.fetch._refuse_rollback`` for the roster — rather than a
fourth, differently-shaped mechanism for the same class of attack.

Why a floor and not ancestry: ``_verify_remote_tag`` fetches ``--depth=1``, so
a merge-base check would have to deepen history on every tick — more git,
more remote-controlled input, and still no answer for a channel switch.

Ordering covers the ``major.minor.patch`` triple only. A pre-release suffix and
its final share a triple and therefore do not advance one another; that refuses
rather than guesses, and the recovery is to cut the next patch version.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, override

from automation.node_config import NodeConfig

_SCHEMA_VERSION: Final = 1
_FLOOR_KEYS: Final = frozenset({"schema_version", "tag", "commit_sha"})
_MAX_TAG_LENGTH: Final = 128

#: Exactly the shape ``automation/public_export.sh:54`` accepts for ``--version``.
#: Anything else never came from the release procedure and is refused rather
#: than ordered by a rule this project has not written down anywhere.
_VERSION_TAG: Final = re.compile(
    r"\Av(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)"
    + r"(?P<suffix>[+-][0-9A-Za-z][0-9A-Za-z.-]*)?\Z"
)
_COMMIT_SHA: Final = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")

_JsonValue: TypeAlias = (
    str | int | float | bool | None | list["_JsonValue"] | dict[str, "_JsonValue"]
)
_JSON_LOADS: Final[Callable[..., _JsonValue]] = json.loads


@dataclass(frozen=True, slots=True)
class ReleaseFloorError(Exception):
    """Same ``(prefix, detail)`` shape the update-trust callers already report."""

    prefix: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.prefix}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ReleaseFloor:
    """The newest release this installation has ever verified."""

    tag: str
    commit_sha: str
    ordering: tuple[int, int, int]


def release_floor_path(config: NodeConfig) -> Path:
    """The one location both verification paths agree on.

    ops owns it, and both callers reach it as ops: the reconciler timer runs as
    ops, and the root helper runs the verifier through ``runuser -u ops``. A
    floor only one of the two paths could read would leave the other exactly as
    exposed as it is today.
    """
    return config.private_root / "deploy-reconcile" / "release-floor.json"


def parse_release_version(tag: str) -> tuple[int, int, int]:
    """Order a release tag, refusing every name the release procedure cannot emit."""
    if len(tag) > _MAX_TAG_LENGTH:
        raise ReleaseFloorError("RELEASE-VERSION", "release tag name is implausibly long")
    matched = _VERSION_TAG.fullmatch(tag)
    if matched is None:
        raise ReleaseFloorError(
            "RELEASE-VERSION",
            f"release tag is not a vX.Y.Z semantic version: {tag}",
        )
    return (
        int(matched.group("major")),
        int(matched.group("minor")),
        int(matched.group("patch")),
    )


def release_floor(tag: str, commit_sha: str) -> ReleaseFloor:
    """Build the floor a verified release establishes."""
    if _COMMIT_SHA.fullmatch(commit_sha) is None:
        raise ReleaseFloorError("RELEASE-FLOOR", "release commit is not an object id")
    return ReleaseFloor(tag=tag, commit_sha=commit_sha, ordering=parse_release_version(tag))


def refuse_release_rollback(floor: ReleaseFloor | None, candidate: ReleaseFloor) -> None:
    """Freshness, not just authorship.

    Equality is accepted on purpose, and only for the identical tag at the
    identical commit. Both verification paths resolve the SAME release every
    tick by design (the ops pre-gate, then the root helper re-verifying to
    close the TOCTOU window), so a strictly-greater rule per resolution would
    refuse the second half of every convergence and freeze the node forever
    after its first successful update. A version number reappearing on a
    different object is not a replay, it is a substitution, and is refused.
    """
    if floor is None:
        return
    if candidate.ordering < floor.ordering:
        raise ReleaseFloorError(
            "RELEASE-ROLLBACK",
            f"release {candidate.tag} does not advance verified release {floor.tag}",
        )
    if candidate.ordering == floor.ordering and (
        candidate.tag != floor.tag or candidate.commit_sha != floor.commit_sha
    ):
        raise ReleaseFloorError(
            "RELEASE-ROLLBACK",
            f"release {candidate.tag} reuses the version of {floor.tag} at another commit",
        )


def advance_release_floor(path: Path, tag: str, commit_sha: str) -> None:
    """Refuse an already-superseded release, then pin the one just verified.

    Called where verification SUCCEEDS, not where convergence does. What prod is
    actually running, and whether its gateway smoke test passed, are separate
    concerns that ``release_rollback`` already owns; making the anti-rollback
    anchor depend on them would give a flaky restart the power to reopen this
    window. Same split as ``managed_sync.state.record_verified`` versus
    ``record_activated``.
    """
    candidate = release_floor(tag, commit_sha)
    floor = load_release_floor(path)
    refuse_release_rollback(floor, candidate)
    if floor is None or candidate.ordering > floor.ordering:
        save_release_floor(path, candidate)


def _invalid(message: str) -> ReleaseFloorError:
    return ReleaseFloorError("RELEASE-FLOOR", f"malformed release floor: {message}")


def _parse_floor(payload: _JsonValue) -> ReleaseFloor:
    if not isinstance(payload, dict) or frozenset(payload) != _FLOOR_KEYS:
        raise _invalid("top-level shape")

    schema_version = payload["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != _SCHEMA_VERSION
    ):
        raise _invalid("schema_version")

    tag = payload["tag"]
    if not isinstance(tag, str):
        raise _invalid("tag")

    commit_sha = payload["commit_sha"]
    if not isinstance(commit_sha, str):
        raise _invalid("commit_sha")

    # A stored tag or commit this module can no longer order is damaged STATE,
    # not a bad incoming release; reporting it as RELEASE-VERSION would aim the
    # operator at origin instead of at the file that actually needs repairing.
    try:
        return release_floor(tag, commit_sha)
    except ReleaseFloorError as error:
        raise _invalid(error.detail) from error


def load_release_floor(path: Path) -> ReleaseFloor | None:
    """Load the floor. Only an ABSENT file is the bootstrap case.

    A file that exists but cannot be understood is refused, never treated as
    "no floor yet" — the silent-reset reading would let anyone who can corrupt
    one byte of this file re-open the very window it closes.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as error:
        raise ReleaseFloorError("RELEASE-FLOOR", f"release floor is not UTF-8: {path}") from error
    except OSError as error:
        raise ReleaseFloorError("RELEASE-FLOOR", f"cannot read release floor: {path}") from error

    try:
        payload = _JSON_LOADS(text)
    except json.JSONDecodeError as error:
        raise ReleaseFloorError(
            "RELEASE-FLOOR",
            f"release floor is not valid JSON: {path}",
        ) from error
    return _parse_floor(payload)


def _refuse_checkout_path(path: Path) -> None:
    try:
        parent = path.resolve().parent
    except OSError as error:
        raise ReleaseFloorError(
            "RELEASE-FLOOR",
            f"cannot resolve release floor path: {path}",
        ) from error
    for candidate in (parent, *parent.parents):
        marker = candidate / ".git"
        if marker.is_dir() or marker.is_file():
            raise ReleaseFloorError(
                "RELEASE-FLOOR",
                f"release floor must live outside a git checkout: {path}",
            )


def save_release_floor(path: Path, floor: ReleaseFloor) -> None:
    """Atomically save the floor with mode 0600, refusing git checkout paths."""
    _refuse_checkout_path(path)
    payload: dict[str, _JsonValue] = {
        "schema_version": _SCHEMA_VERSION,
        "tag": floor.tag,
        "commit_sha": floor.commit_sha,
    }
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.name}.",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                _ = temporary.write(serialized)
                temporary.flush()
                _ = os.fsync(temporary.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    temporary_path = None
    except OSError as error:
        raise ReleaseFloorError("RELEASE-FLOOR", f"cannot save release floor: {path}") from error
