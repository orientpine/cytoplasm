"""The one command the watcher may run once the owner has approved.

FA-3. A resume is a plain re-invocation of the existing deploy pipeline. That is the
entire safety argument: the pipeline re-verifies the owner decision (including stop
precedence), the peer attestation, the review verdict and the artifact digest before it
installs anything, so the watcher never becomes a second, weaker copy of the gate.

Which means the command must carry nothing that would skip any of that. The pipeline
offers several ways to stop early, to repost instead of resume, to supersede the very
request being acted on, to retarget the source directory, and to switch off the
provenance guard. None of those is something a resume could legitimately want, and the
accompanying test asserts their absence from this module's *source* rather than from
one constructed command — an automation that can reach a bypass eventually does.

Refusing an unsupported kind here matters for the same reason. It is the last point
before invented arguments would reach a subprocess: the kinds this watcher cannot drive
from a record alone need context that lives outside the record, and guessing it would
mean mounting something nobody authorised.

The command escalates, because it must. The watcher runs as the account that owns the
gate records and that account has no sudo at all, while the pipeline escalates per step
(peer for the sandbox, ops for the release converge). So the resume goes through ONE
root-owned helper with ONE argument shape rather than handing `agent` a shell as ops —
that account executes LLM-directed code, and ops reaches the repair push key and the
release-install grant. Triggering still is not authorizing: the helper sources the skill
from the sealed release, whose tree must byte-match origin/main.
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

from automation.supply_chain_plan import SUPPORTED_KINDS, PendingRequest


class UnsupportedResume(Exception):
    """This kind cannot be driven from a pending record, so there is no command."""


def resume_command(resume_helper: Path, request: PendingRequest) -> tuple[str, ...]:
    """The privileged edge, spelled out: sudo, the one helper, and the skill.

    The skill is ``request.name``. For a deploy that happens to equal the record's file
    name, but the pipeline takes a skill, and saying so keeps the two from being
    conflated the day they diverge.

    ``-n`` is not decoration: a helper that could prompt would hang a timer tick forever
    instead of failing, and a hung tick reports nothing at all.
    """
    if request.kind not in SUPPORTED_KINDS:
        raise UnsupportedResume(f"{request.key}: {request.kind} cannot resume from a record")
    return ("sudo", "-n", str(resume_helper), "--skill", request.name)


#: ``EXECUTION-LOCK-BLOCK`` — another execution holds this skill's lease. Nothing is
#: wrong; someone else is mid-deploy.
LEASE_CONTENTION_EXIT: Final = 8

#: The owner put the stop reaction on the request. The decision has been made.
CANCELLED_EXIT: Final = 9


def interpret_resume(exit_code: int) -> str:
    """``done`` | ``retry`` | ``retire`` | ``failed`` — what that attempt meant.

    The two codes worth staring at are 8 and 9, which look alike and mean opposite
    things. Reading contention as cancellation retires a request whose approval is
    still live and still wanted; reading cancellation as contention re-invokes the
    pipeline against a deploy the owner stopped, forever. They were the same number
    for part of this cycle, which is how the distinction earned its own table.

    Everything unrecognised is ``failed``, which RETAINS the record. Nothing maps to a
    silent drop: an approval the owner already gave must never disappear because the
    pipeline returned a number this table had not met before.
    """
    if exit_code == 0:
        return "done"
    if exit_code == LEASE_CONTENTION_EXIT:
        return "retry"
    if exit_code == CANCELLED_EXIT:
        return "settled"
    return "failed"
