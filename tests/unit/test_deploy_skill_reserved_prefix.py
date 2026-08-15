"""A skill name must not be able to collide with another kind's approval record.

Pending approval records live at ``<gate_dir>/pending/<record_name>.json``, and the
record name is the skill for a deploy but ``publish-<skill>`` for a publish
(``skill_gate_specs.py:99-100,175-176``). So a skill literally named ``publish-x``
writes to the same path as the publish record for skill ``x`` — two different owner
authorisations contending for one file on the highest-privilege path in the system.

``managed-`` is already reserved for exactly this class of reason and is guarded in
``deploy-skill.sh``. ``publish-`` was not, purely because nobody had needed the second
prefix yet. This closes it the same way rather than inventing a new mechanism.

The guard is unconditional, unlike the ``managed-`` one. That guard only protects the
mount path, because a managed name is legal — it just needs the right flag. A
``publish-`` name is not legal at all under this layout, and the damage is done the
moment a record is written, which happens long before mounting.
"""
from __future__ import annotations

from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[2] / "automation" / "deploy-skill.sh"
_SOURCE = _DEPLOY.read_text(encoding="utf-8")


def _index(needle: str) -> int:
    position = _SOURCE.find(needle)
    assert position != -1, f"missing from deploy-skill.sh: {needle!r}"
    return position


def test_a_publish_prefixed_skill_name_is_refused() -> None:
    assert '"$SKILL" == publish-*' in _SOURCE


def test_the_refusal_is_fatal_and_named() -> None:
    guard = _index('"$SKILL" == publish-*')
    assert "RESERVED-BLOCK" in _SOURCE[guard : guard + 400]


def test_the_guard_is_unconditional() -> None:
    """A publish- name is illegal on every path, not only when mounting.

    Gating it on a flag the way ``managed-`` is gated would let --request-only write
    the colliding record and exit 0, which is the failure this exists to prevent.
    """
    guard = _index('"$SKILL" == publish-*')
    line_start = _SOURCE.rfind("\n", 0, guard) + 1
    line = _SOURCE[line_start : _SOURCE.find("\n", guard)]
    for flag in ("ACTIVATE_MANAGED", "APPROVE_ONLY", "SANDBOX_ONLY", "REQUEST_ONLY"):
        assert flag not in line, f"guard must not depend on {flag}: {line}"


def test_the_guard_runs_before_anything_writes_a_record() -> None:
    """Records are written by the request stage; refusing later is refusing too late."""
    guard = _index('"$SKILL" == publish-*')
    assert guard < _index("check_with_attestation_refresh ")
    assert guard < _index("REQUEST_ONLY\" == 1")


def test_the_existing_managed_reservation_is_untouched() -> None:
    """This adds a second reserved prefix; it does not rework the first."""
    assert '"$SKILL" == managed-*' in _SOURCE
    assert "MANAGED-BLOCK: mounting a managed skill requires --activate-managed" in _SOURCE
