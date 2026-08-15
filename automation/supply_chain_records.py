"""Reading the pending-record directory as work, without deciding anything about it.

FA-3. The watcher is the first thing that ENUMERATES approval records instead of
opening one by name, which is why the layout had to be made unambiguous first:
``publish-`` is now a reserved skill-name prefix, the way ``managed-`` already was, so
a record file name maps to exactly one kind.

Two details are load-bearing and easy to get subtly wrong:

* **kind is derived from the name, and a canonical resolver already exists.**
  ``skill_gate_surface.deploy_kind`` decides SKILL_DEPLOY vs MANAGED_ACTIVATE from the
  ``managed-`` prefix. This module calls it rather than re-deriving the rule, because a
  second copy of a rule is the copy that rots when the rule changes.
* **the key is NOT the kind.** ``DeploySpec.key()`` returns ``skill-deploy:<name>`` for
  managed skills too — it does not branch. A managed activation is therefore keyed
  ``skill-deploy:managed-x`` while its kind is ``managed-activate``. Conflating them
  would send the watcher looking for a record that does not exist.

Enumeration is deliberately dumb about content: it reads names, not JSON. A file that
is not a usable record is still enumerated, and the decision layer retains it as
undecidable. That is the safe direction — dropping it here would silently strand an
approval the owner already gave, leaving nothing anywhere to show that it happened.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Generic, TypeVar

from automation.skill_gate_surface import deploy_kind
from automation.supply_chain_plan import PendingRequest

#: Mirrors ``PublishSpec.record_name()``. Unambiguous only because a skill may not be
#: named ``publish-*`` — deploy-skill.sh refuses that outright (RESERVED-BLOCK).
PUBLISH_PREFIX: Final = "publish-"

#: ``PublishSpec.key()`` / ``DeploySpec.key()`` respectively. The deploy key is used for
#: managed activations too, because the spec that writes it does not branch.
PUBLISH_KEY: Final = "skill-publish"
DEPLOY_KEY: Final = "skill-deploy"
RequestT = TypeVar("RequestT")


@dataclass(frozen=True, slots=True)
class EnumerationResult(Generic[RequestT]):
    """Items found by one directory enumeration and whether that enumeration succeeded."""

    requests: tuple[RequestT, ...]
    succeeded: bool

    def __iter__(self) -> Iterator[RequestT]:
        return iter(self.requests)

    def __len__(self) -> int:
        return len(self.requests)


def _request_for(record_name: str) -> PendingRequest:
    if record_name.startswith(PUBLISH_PREFIX):
        skill = record_name.removeprefix(PUBLISH_PREFIX)
        return PendingRequest(
            key=f"{PUBLISH_KEY}:{skill}",
            kind=PUBLISH_KEY,
            name=skill,
            record_name=record_name,
        )
    # Deploy family. The kind may still be a managed activation; the key never is.
    return PendingRequest(
        key=f"{DEPLOY_KEY}:{record_name}",
        kind=deploy_kind(record_name).value,
        name=record_name,
        record_name=record_name,
    )


def enumerate_pending(gate_dir: Path) -> EnumerationResult[PendingRequest]:
    """Every live approval request under ``gate_dir``, in a stable order.

    Sorted because a watcher that reorders its work reorders which per-skill execution
    lock it contends for first, which turns a reproducible tick into a flaky one.
    """
    directory = gate_dir / "pending"
    try:
        entries = sorted(directory.glob("*.json"))
        requests = tuple(_request_for(entry.stem) for entry in entries if entry.is_file())
    except OSError:
        return EnumerationResult((), False)
    return EnumerationResult(requests, True)
