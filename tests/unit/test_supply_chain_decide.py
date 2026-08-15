"""Reading the owner's answer for an enumerated request, and refusing to guess one.

FA-3. ``plan_tick`` asks a ``Decide`` callable what the owner said. This is the adapter
that answers by opening the request's record and asking the gate about the message the
record is bound to.

Every path that is not "the gate told us" must raise rather than return. ``plan_tick``
turns a raised exception into ``retain``, which costs one more tick; returning a guess
costs a deploy nobody authorised. So a missing record, unreadable JSON, or a record
with no ``message_id`` are all errors here — deliberately never ``absent``, which is a
real answer meaning "the owner has not reacted yet".

The adapter never re-derives the record's file name: ``record_name`` is carried on the
request precisely so this layer can open the file for any kind without knowing how
names map to kinds.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.supply_chain_decide import make_decider
from automation.supply_chain_plan import PendingRequest

_DEPLOY = PendingRequest(
    key="skill-deploy:demo", kind="skill-deploy", name="demo", record_name="demo"
)
_PUBLISH = PendingRequest(
    key="skill-publish:demo", kind="skill-publish", name="demo", record_name="publish-demo"
)
_MESSAGE = "777777777777777777"


def _write_record(root: Path, record_name: str, payload: dict[str, str]) -> None:
    directory = root / "pending"
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / f"{record_name}.json").write_text(json.dumps(payload), encoding="utf-8")


class _Gate:
    def __init__(self, answer: str) -> None:
        self.asked: list[str] = []
        self._answer = answer

    def __call__(self, message_id: str) -> str:
        self.asked.append(message_id)
        return self._answer


def test_the_decision_comes_from_the_message_the_record_is_bound_to(tmp_path: Path) -> None:
    _write_record(tmp_path, "demo", {"message_id": _MESSAGE, "hash": "a" * 64})
    gate = _Gate("approved")
    assert make_decider(tmp_path, decision_of=gate)(_DEPLOY) == "approved"
    assert gate.asked == [_MESSAGE]


def test_a_publish_record_is_opened_by_its_own_file_name(tmp_path: Path) -> None:
    """The skill is `demo`; the file is `publish-demo.json`. No re-derivation here."""
    _write_record(tmp_path, "publish-demo", {"message_id": _MESSAGE})
    gate = _Gate("denied")
    assert make_decider(tmp_path, decision_of=gate)(_PUBLISH) == "denied"
    assert gate.asked == [_MESSAGE]


def test_every_answer_is_passed_through_verbatim(tmp_path: Path) -> None:
    _write_record(tmp_path, "demo", {"message_id": _MESSAGE})
    for answer in ("approved", "denied", "absent"):
        assert make_decider(tmp_path, decision_of=_Gate(answer))(_DEPLOY) == answer


def test_a_missing_record_raises_rather_than_answering(tmp_path: Path) -> None:
    """The request vanished mid-tick. That is not the owner declining to react."""
    gate = _Gate("approved")
    with pytest.raises(Exception):
        _ = make_decider(tmp_path, decision_of=gate)(_DEPLOY)
    assert gate.asked == []


def test_an_unreadable_record_raises_rather_than_answering(tmp_path: Path) -> None:
    directory = tmp_path / "pending"
    directory.mkdir(parents=True)
    _ = (directory / "demo.json").write_text("{not json", encoding="utf-8")
    gate = _Gate("approved")
    with pytest.raises(Exception):
        _ = make_decider(tmp_path, decision_of=gate)(_DEPLOY)
    assert gate.asked == []


def test_a_record_without_a_message_id_raises(tmp_path: Path) -> None:
    """Nothing to ask about is not permission to proceed."""
    _write_record(tmp_path, "demo", {"hash": "a" * 64})
    gate = _Gate("approved")
    with pytest.raises(Exception):
        _ = make_decider(tmp_path, decision_of=gate)(_DEPLOY)
    assert gate.asked == []


def test_a_non_object_record_raises(tmp_path: Path) -> None:
    directory = tmp_path / "pending"
    directory.mkdir(parents=True)
    _ = (directory / "demo.json").write_text("[]", encoding="utf-8")
    with pytest.raises(Exception):
        _ = make_decider(tmp_path, decision_of=_Gate("approved"))(_DEPLOY)


def test_the_gate_is_never_consulted_when_the_record_is_unusable(tmp_path: Path) -> None:
    """A Discord round-trip for a record we cannot bind to is wasted and misleading."""
    gate = _Gate("approved")
    decider = make_decider(tmp_path, decision_of=gate)
    for request in (_DEPLOY, _PUBLISH):
        with pytest.raises(Exception):
            _ = decider(request)
    assert gate.asked == []
