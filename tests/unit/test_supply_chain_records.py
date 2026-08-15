"""Turning the pending-record directory into work the watcher can reason about.

FA-3. The watcher is the first thing that ENUMERATES approval records rather than
opening one by name, which is why the record layout had to be made unambiguous first
(``publish-`` is now a reserved skill prefix, like ``managed-``).

Two details here are easy to get subtly wrong, and both are load-bearing:

* **kind is derived from the name, and there is already a canonical function for it.**
  ``skill_gate_surface.deploy_kind`` decides SKILL_DEPLOY vs MANAGED_ACTIVATE from the
  ``managed-`` prefix. Re-deriving that rule here would produce a second copy, and the
  second copy is the one that rots when the rule changes.
* **the key is NOT the kind.** ``DeploySpec.key()`` returns ``skill-deploy:<name>`` for
  managed skills too — it does not branch. So a managed activation is keyed
  ``skill-deploy:managed-x`` while its kind is ``managed-activate``. Conflating them
  would make the watcher look up the wrong record.

Enumeration is deliberately dumb about content: it reads names, not JSON. A file that
is not a usable record still gets enumerated, and the decision layer retains it as
undecidable. That is the safe direction — dropping it here would mean an approval the
owner already gave silently never gets acted on, with nothing to show for it.
"""
from __future__ import annotations

from pathlib import Path

from automation.skill_gate_surface import deploy_kind
from automation.supply_chain_records import enumerate_pending


def _pending(root: Path) -> Path:
    directory = root / "pending"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write(root: Path, name: str) -> None:
    _ = (_pending(root) / f"{name}.json").write_text("{}", encoding="utf-8")


def test_a_missing_gate_directory_yields_nothing(tmp_path: Path) -> None:
    assert enumerate_pending(tmp_path / "absent").requests == ()


def test_an_empty_pending_directory_yields_nothing(tmp_path: Path) -> None:
    _ = _pending(tmp_path)
    assert enumerate_pending(tmp_path).requests == ()


def test_a_bare_name_is_a_skill_deploy(tmp_path: Path) -> None:
    _write(tmp_path, "demo")
    (request,) = enumerate_pending(tmp_path)
    assert request.kind == "skill-deploy"
    assert request.name == "demo"
    assert request.key == "skill-deploy:demo"


def test_a_managed_name_is_a_managed_activation_but_keeps_the_deploy_key(
    tmp_path: Path,
) -> None:
    """DeploySpec.key() does not branch on managed, so neither may enumeration."""
    _write(tmp_path, "managed-demo")
    (request,) = enumerate_pending(tmp_path)
    assert request.kind == "managed-activate"
    assert request.name == "managed-demo"
    assert request.key == "skill-deploy:managed-demo"


def test_a_publish_record_is_a_publish_of_the_underlying_skill(tmp_path: Path) -> None:
    _write(tmp_path, "publish-demo")
    (request,) = enumerate_pending(tmp_path)
    assert request.kind == "skill-publish"
    assert request.name == "demo"
    assert request.key == "skill-publish:demo"


def test_the_kind_matches_the_canonical_resolver(tmp_path: Path) -> None:
    """If deploy_kind ever changes, this must follow it rather than disagree."""
    for name in ("demo", "managed-demo"):
        _write(tmp_path, name)
    by_name = {request.name: request.kind for request in enumerate_pending(tmp_path)}
    assert by_name == {name: deploy_kind(name).value for name in ("demo", "managed-demo")}


def test_enumeration_is_deterministic(tmp_path: Path) -> None:
    """A watcher that reorders its work reorders which lock it contends for first."""
    for name in ("zeta", "alpha", "managed-mid"):
        _write(tmp_path, name)
    assert [request.name for request in enumerate_pending(tmp_path)] == [
        "alpha",
        "managed-mid",
        "zeta",
    ]


def test_non_json_entries_are_ignored(tmp_path: Path) -> None:
    directory = _pending(tmp_path)
    _ = (directory / "notes.txt").write_text("x", encoding="utf-8")
    (directory / "a-directory.json").mkdir()
    _write(tmp_path, "demo")
    assert [request.name for request in enumerate_pending(tmp_path)] == ["demo"]


def test_an_unreadable_record_is_still_enumerated(tmp_path: Path) -> None:
    """Dropping it here would silently strand an approval the owner already gave."""
    directory = _pending(tmp_path)
    _ = (directory / "broken.json").write_text("{not json", encoding="utf-8")
    assert [request.name for request in enumerate_pending(tmp_path)] == ["broken"]


def test_the_record_name_is_carried_not_reconstructed(tmp_path: Path) -> None:
    """Whoever reopens the record must not have to re-derive the filename.

    ``name`` is the SKILL for a publish request (``publish-demo.json`` -> ``demo``), so
    anything wanting to read that record again would have to re-apply the ``publish-``
    rule — a second copy of a rule this module's own docstring warns against. The
    enumerator already holds the filename, so it carries it instead.
    """
    for name in ("demo", "managed-demo", "publish-demo"):
        _write(tmp_path, name)
    carried = {request.record_name for request in enumerate_pending(tmp_path)}
    assert carried == {"demo", "managed-demo", "publish-demo"}


def test_every_record_name_resolves_back_to_its_file(tmp_path: Path) -> None:
    """The carried name must be openable as-is, for every family."""
    for name in ("demo", "managed-demo", "publish-demo"):
        _write(tmp_path, name)
    for request in enumerate_pending(tmp_path):
        assert (tmp_path / "pending" / f"{request.record_name}.json").is_file()
