"""What a supply-chain watcher decides — deliberately separated from what it may do.

FA-3. This is the piece that makes ✅ final. Today nobody re-runs the pipeline when the
owner reacts on the supply-chain approval surface, so an approval sits unread until a
human tells a session about it — the round-trip that made the owner's ✅ feel
provisional. The owner-DM flows have had reaction watchers for months; the skill supply
chain, which is the highest privilege path in the system, never got one.

That privilege is exactly why the decision lives here, alone, with no ability to act.
The vocabulary is three words:

* ``resume`` re-invokes the EXISTING pipeline, which re-verifies the owner decision, the
  peer attestation, the review verdict and the digest before it installs anything. There
  is deliberately no ``mount``: a watcher that could mount would be a second, weaker copy
  of the gate, and the second copy is always the one that rots.
* ``settled`` is a decision the owner already made. It carries NO side effect, because
  measurement found no primitive that means "the owner said no": ``consume`` retires
  what a MOUNT consumed and a denial mounted nothing, while ``abandon`` is an operator
  override whose audit records ``SUDO_USER`` as the authority — a watcher calling it
  would attribute the act to a human who did nothing. None is needed: the pending record
  IS the durable refusal. The stop reaction wins forever, and a content change supersedes
  the record on its own because the digest is a hash input. Deleting it would post a new
  request for the same bytes and ask the owner to decide again what they already decided.
* ``retain`` is the answer to every uncertainty — unsupported kind, unreadable reaction,
  transport failure, a decision string we do not recognise. Retaining costs one more
  tick. Guessing costs a deploy nobody authorised.

Notably absent: any action that posts. Re-posting or superseding a live request is what
the 승인 메시지 단일성 규칙 forbids, and the way to be certain a watcher never does it is
to give it no word for it. Same reasoning for channel history: this function receives
records and a per-record decision callable, so there is no seam through which it could
discover work by scanning messages (AGENTS.md: 워처는 리액션만 폴링).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final, TypeAlias

#: Kinds this watcher can actually RESUME from a pending record alone. WHICH channel
#: the records belong to is `approval_surface.py`'s call, the single decider — naming a
#: physical surface here would couple runtime code to it, which conformance forbids.
#:
#: Only `skill-deploy` qualifies, measured 2026-08-01:
#:   * `managed-activate` needs `--activate-managed <quarantine-dir>`, a directory
#:     holding manifest.json / provenance.json / <skill>/SKILL.md that the managed-sync
#:     fetch step produces. Its path appears nowhere in the pending record.
#:   * `skill-publish` resumes through a different program (`managed_skills/publish_cli`)
#:     which requires `--managed-repo`.
#:   * `skill-attest` is not an owner decision at all — a peer bot posts a bound verdict,
#:     and the deploy path already refreshes it when the TTL expires.
#:
#: Listing a kind here promises an adapter. Promising one that cannot be written yet
#: would have the watcher invoke something with invented arguments; retaining instead
#: keeps the request live so a human can still finish it.
SUPPORTED_KINDS: Final = frozenset({"skill-deploy"})

RESUME: Final = "resume"
SETTLED: Final = "settled"
RETAIN: Final = "retain"


@dataclass(frozen=True, slots=True)
class PendingRequest:
    """A live approval request, identified by what the gate already stores.

    ``name`` is the SKILL, which for a publish request is not the record's file name
    (``publish-demo.json`` describes skill ``demo``). ``record_name`` is therefore
    carried rather than re-derived: anything that reopens the record would otherwise
    have to re-apply the ``publish-`` rule, and a second copy of a rule is the copy
    that rots when the rule changes.
    """

    key: str
    kind: str
    name: str
    record_name: str


@dataclass(frozen=True, slots=True)
class Plan:
    """One decision for one request. The reason is for the journal, not for control flow."""

    request: PendingRequest
    action: str
    reason: str


Decide: TypeAlias = Callable[[PendingRequest], str]
"""``approved`` | ``denied`` | ``missing`` | ``absent``. May raise — that is not permission."""


def _plan_for(request: PendingRequest, decide: Decide) -> Plan:
    if request.kind not in SUPPORTED_KINDS:
        # An adapter that does not exist must not be improvised. Note this returns
        # before `decide` runs: an unsupported kind should not even cost a lookup.
        return Plan(request, RETAIN, f"unsupported-kind:{request.kind}")
    try:
        decision = decide(request)
    except Exception as error:  # noqa: BLE001 - any failure means "no answer", never "yes"
        return Plan(request, RETAIN, f"undecidable:{type(error).__name__}")
    if decision == "approved":
        return Plan(request, RESUME, "owner-approved")
    if decision == "denied":
        return Plan(request, SETTLED, "owner-denied")
    if decision == "absent":
        return Plan(request, RETAIN, "unanswered")
    if decision == "missing":
        # A deleted message cannot change on a later poll. Preserve the record for an
        # owner-driven re-post, but classify it as terminal rather than transient retry.
        return Plan(request, SETTLED, "approval-message-missing")
    # Only the exact vocabulary counts. A decision we cannot read is not an approval.
    return Plan(request, RETAIN, f"unreadable-decision:{decision!r}")


def plan_tick(requests: Iterable[PendingRequest], *, decide: Decide) -> tuple[Plan, ...]:
    """Exactly one plan per request, in the order given.

    One plan per request is a contract, not an implementation detail: two plans for the
    same key would mean the effects layer invokes the same pipeline twice in a tick, and
    the second invocation would race the first for the per-skill execution lock.
    """
    return tuple(_plan_for(request, decide) for request in requests)
