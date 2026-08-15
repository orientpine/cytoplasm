"""Reading the owner's answer for an enumerated request — and never inventing one.

FA-3. ``plan_tick`` asks a ``Decide`` callable what the owner said; this is the adapter
that answers by opening the request's record and asking the gate about the message that
record is bound to.

The shape here is chosen by what the failure modes cost. ``plan_tick`` converts a raised
exception into ``retain``, so raising costs one more tick — while returning a guess
costs a deploy nobody authorised, on the path that mounts code. Therefore every state
that is not "the gate told us" raises: a missing record, unreadable JSON, a payload that
is not an object, a record with no ``message_id``.

In particular none of those is ``absent``. ``absent`` is a real answer meaning the owner
has not reacted yet, and a watcher that conflates "we could not look" with "they have
not answered" will keep a broken request alive forever while reporting it as healthy.

The gate is not consulted at all when the record is unusable: a round-trip asking about
a message we cannot bind to is wasted, and it would put a misleading lookup in the log
for a request that was never actionable.

Record file names are never re-derived here. ``PendingRequest.record_name`` is carried
precisely so this layer opens the right file for any kind without knowing how names map
to kinds — the ``publish-`` asymmetry lives in exactly one place.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

from automation.supply_chain_plan import Decide, PendingRequest

MESSAGE_ID_FIELD = "message_id"


class UndecidableRequest(Exception):
    """The record could not be bound to a message, so there is no answer to read."""


def _load_record(gate_dir: Path, request: PendingRequest) -> Mapping[str, object]:
    path = gate_dir / "pending" / f"{request.record_name}.json"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UndecidableRequest(f"{request.key}: record unreadable ({path})") from error
    if not isinstance(decoded, dict):
        raise UndecidableRequest(f"{request.key}: record is not an object ({path})")
    return decoded


def bound_message_id(gate_dir: Path, request: PendingRequest) -> str:
    """The message this request's owner decision lives on."""
    record = _load_record(gate_dir, request)
    message_id = record.get(MESSAGE_ID_FIELD)
    if not isinstance(message_id, str) or not message_id:
        raise UndecidableRequest(f"{request.key}: record has no {MESSAGE_ID_FIELD}")
    return message_id


def make_decider(gate_dir: Path, *, decision_of: Callable[[str], str]) -> Decide:
    """Bind a per-message decision lookup into the ``Decide`` shape ``plan_tick`` wants.

    ``decision_of`` is injected rather than imported so the gate's Discord round-trip
    stays out of the decision logic — and so this is testable without a network.
    """

    def decide(request: PendingRequest) -> str:
        return decision_of(bound_message_id(gate_dir, request))

    return decide
