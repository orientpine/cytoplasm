"""A stale approval cannot authorise changed content. This pins the property, not a fix.

FA-2. The plan (and Oracle) assumed a resume path could mount new source under an old
✅, and that avoiding it needed the pending record to carry a durable copy of the
approved artifact. Measurement said otherwise: the protection already exists, spread
across three places that each look incidental on their own.

    action_hash = _hash("skill-deploy", skill, DIGEST, provenance.tag, manifest_sha256)

so the owner's decision is bound to the bytes; ``reuse()`` revives a request only while
that hash still matches; and ``deploy-skill.sh`` recomputes the digest before the
install and refuses on ``REVIEW-BLOCK``. Together they mean the failure mode is "cannot
resume", never "mounted the wrong thing".

Because the property is emergent rather than declared, it is easy to break while
believing you are simplifying — drop the digest from the hash inputs and every test
still passes, every deploy still works, and a stale ✅ silently starts authorising
whatever is on disk. Hence these pins. They were GREEN the moment they were written:
that is the point, and the mutation each one describes is what they exist to catch.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from automation.skill_gate_request import reuse
from automation.skill_gate_specs import DeploySpec, Provenance

_APPROVED_DIGEST = "a" * 64
_CHANGED_DIGEST = "b" * 64
_MESSAGE = "555555555555555555"
_NONCE = "deadbeefdeadbeef"


def _spec(digest: str) -> DeploySpec:
    return DeploySpec(
        skill="demo",
        digest=digest,
        deploy_nonce=_NONCE,
        review_status="- review: PASS",
        provenance=Provenance(lines="", tag="v1", manifest_sha256="c" * 64),
        binding=re.compile(r"demo"),
    )


class _Gate:
    """Only what ``reuse`` touches: the stored record and the spec to compare it against."""

    def __init__(self, spec: DeploySpec, record: dict[str, str] | None) -> None:
        self.spec = spec
        self._record = record

    def stored(self) -> dict[str, str] | None:
        return self._record


def _record_for(spec: DeploySpec) -> dict[str, str]:
    return {
        "hash": spec.digest,
        "message_id": _MESSAGE,
        "deploy_nonce": _NONCE,
        "action_hash": spec.action_hash(),
    }


def test_the_action_hash_is_bound_to_the_content_digest() -> None:
    """Drop the digest from the hash inputs and a stale ✅ starts authorising new bytes."""
    approved = _spec(_APPROVED_DIGEST)
    changed = replace(approved, digest=_CHANGED_DIGEST)
    assert approved.action_hash() != changed.action_hash()


def test_the_action_hash_is_stable_when_nothing_changed() -> None:
    """Otherwise every re-run would supersede, and the owner would re-approve constantly."""
    assert _spec(_APPROVED_DIGEST).action_hash() == _spec(_APPROVED_DIGEST).action_hash()


def test_a_changed_source_cannot_reuse_the_approved_request() -> None:
    """The core property: the owner approved bytes, not a skill name."""
    approved = _spec(_APPROVED_DIGEST)
    changed = replace(approved, digest=_CHANGED_DIGEST)
    gate: Any = _Gate(changed, _record_for(approved))
    assert reuse(gate) is None


def test_an_unchanged_source_reuses_the_live_request() -> None:
    """No duplicate post — the 승인 메시지 단일성 규칙 depends on this being a reuse."""
    approved = _spec(_APPROVED_DIGEST)
    gate: Any = _Gate(approved, _record_for(approved))
    outcome = reuse(gate)
    assert outcome is not None
    assert outcome.record["message_id"] == _MESSAGE


def test_provenance_is_part_of_the_binding_too() -> None:
    """A managed release retagged under the same bytes is a different authorisation."""
    approved = _spec(_APPROVED_DIGEST)
    retagged = replace(approved, provenance=replace(approved.provenance, tag="v2"))
    assert approved.action_hash() != retagged.action_hash()
