"""New cards select a durable render version before resolving any surface."""
from __future__ import annotations

import builtins
from dataclasses import replace
from unittest.mock import Mock

import pytest

from tests.unit.test_wiki_patent_owner_cards import (
    WIKI_V1, PATENT_V1, draft, manifest, wiki_gate, patent_export, patent_export_gate, pm,
)
from tests.unit.test_wiki_patent_approval_characterization import wiki_env as wiki_env, _new_draft
from tests.unit.test_patent_export_binding import env as env, SLUG


def test_wiki_version_when_new_card_is_posted(wiki_env) -> None:
    # Given
    record = _new_draft()
    # When
    wiki_gate.post_confirm_message(record)
    # Then
    stored = wiki_gate.load_draft(record["id"])
    assert stored.get("render_version") == 2
    assert wiki_env.contents[stored["confirm_message_id"]] == wiki_gate.confirm_text(stored)


def test_patent_version_when_new_card_is_posted(env) -> None:
    # Given / When
    patent_export.prepare_export(env.paths, SLUG, mode="enc")
    # Then
    stored = pm.load_manifest(SLUG)
    assert stored.render_version == 2
    assert env.fake.messages[stored.message_id][1] == patent_export.render_approval(stored)


@pytest.mark.parametrize("producer", ["wiki", "patent"])
def test_no_surface_when_every_render_refuses(producer, monkeypatch, wiki_env, env) -> None:
    # Given: refusal must precede both thread resolution and posting.
    def refuse(*args, **kwargs):
        if producer == "wiki":
            raise wiki_gate.GateError("unrenderable")
        raise patent_export_gate.ExportGateError("unrenderable")
    # When / Then
    if producer == "wiki":
        monkeypatch.setattr(wiki_gate, "confirm_text", refuse)
        with pytest.raises(wiki_gate.GateError):
            wiki_gate.post_confirm_message(_new_draft())
        assert wiki_env.threads == []
        assert wiki_env.contents == {}
    else:
        monkeypatch.setattr(patent_export, "render_approval", refuse)
        with pytest.raises(patent_export_gate.ExportGateError):
            patent_export.prepare_export(env.paths, SLUG, mode="enc")
        assert env.fake.threads == []
        assert env.fake.messages == {}


@pytest.mark.parametrize("producer", ["wiki", "patent"])
def test_unknown_version_when_replaying_refuses(producer) -> None:
    # Given / When / Then
    if producer == "wiki":
        with pytest.raises(wiki_gate.GateError):
            wiki_gate.confirm_text(draft() | {"render_version": 99})
    else:
        with pytest.raises(patent_export_gate.ExportGateError):
            patent_export.render_approval(replace(manifest(), render_version=99))


@pytest.mark.parametrize("failure", ["import", "render"])
def test_fallback_versions_when_owner_envelope_unavailable(failure, monkeypatch, wiki_env, env) -> None:
    # Given
    from automation.interop import owner_message as om
    original_import = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "automation.interop.owner_message":
            raise ImportError(name)
        return original_import(name, *args, **kwargs)
    def broken(*args, **kwargs):
        raise om.OwnerMessageError(detail="fixture")
    if failure == "import":
        monkeypatch.setattr(builtins, "__import__", missing)
    else:
        monkeypatch.setattr(om, "render", broken)
    record = _new_draft()
    # When
    wiki_gate.post_confirm_message(record)
    patent_export.prepare_export(env.paths, SLUG, mode="enc")
    # Then
    wiki = wiki_gate.load_draft(record["id"])
    patent = pm.load_manifest(SLUG)
    assert wiki.get("render_version") == 1
    assert patent.render_version == 1
    assert wiki_gate.confirm_text(draft()) == WIKI_V1
    assert patent_export.render_approval(manifest()) == PATENT_V1
    assert record["sha256"] in wiki_env.contents[wiki["confirm_message_id"]]
    assert patent_export_gate.approval_binding_matches(patent, env.fake.messages[patent.message_id][1])


@pytest.mark.parametrize("producer", ["wiki", "patent"])
def test_legacy_card_when_repeated_request_keeps_bytes(producer, monkeypatch, wiki_env, env) -> None:
    # Given: a card already posted by an older runtime, without an owner reaction.
    from automation.interop import owner_message as om
    def broken(*args, **kwargs):
        raise om.OwnerMessageError(detail="fixture")
    wiki_env.approve_users = []
    record = _new_draft()
    with monkeypatch.context() as old:
        old.setattr(om, "render", broken)
        if producer == "wiki":
            wiki_gate.post_confirm_message(record)
        else:
            patent_export.prepare_export(env.paths, SLUG, mode="enc")
    if producer == "wiki":
        before = dict(wiki_env.contents)
        stored = wiki_gate.load_draft(record["id"])
        # When
        wiki_gate.post_confirm_message(stored)
        # Then
        assert wiki_env.contents == before
        assert wiki_gate.load_draft(record["id"]) == stored
    else:
        before = dict(env.fake.messages)
        stored = pm.load_manifest(SLUG)
        # When
        patent_export.prepare_export(env.paths, SLUG, mode="enc")
        # Then
        assert env.fake.messages == before
        assert pm.load_manifest(SLUG) == stored


@pytest.mark.parametrize("producer", ["wiki", "patent"])
@pytest.mark.parametrize("refuses", [False, True])
def test_bound_reuse_never_calls_renderer(producer: str, refuses: bool, monkeypatch: pytest.MonkeyPatch, wiki_env, env) -> None:
    # Given: a real posted v2 card and the real lifecycle/store, only transport is fake.
    wiki_env.approve_users = []
    record = _new_draft()
    if producer == "wiki":
        wiki_gate.post_confirm_message(record)
        stored = wiki_gate.load_draft(record["id"])
        before = dict(wiki_env.contents)
        target, name = wiki_gate, "confirm_text"
        error = wiki_gate.GateError("renderer-unavailable")
    else:
        patent_export.prepare_export(env.paths, SLUG, mode="enc")
        stored = pm.load_manifest(SLUG)
        before = dict(env.fake.messages)
        target, name = patent_export, "render_approval"
        error = patent_export_gate.ExportGateError("renderer-unavailable")
    renderer = Mock(side_effect=error) if refuses else Mock(wraps=getattr(target, name))
    monkeypatch.setattr(target, name, renderer)
    # When: even a renderer that would refuse must not affect this bound request.
    try:
        if producer == "wiki":
            reused = wiki_gate.post_confirm_message(stored)
        else:
            reused = patent_export.prepare_export(env.paths, SLUG, mode="enc")
    finally:
        # A successful reuse alone cannot prove the renderer was never called.
        assert renderer.call_count == 0
    # Then: the original message and durable binding remain intact; no repost.
    if producer == "wiki":
        assert reused["confirm_message_id"] == stored["confirm_message_id"]
        assert wiki_env.contents == before
        assert wiki_gate.load_draft(record["id"]) == stored
    else:
        assert f"sha256={stored.plaintext_sha256}" in reused
        assert env.fake.messages == before
        assert pm.load_manifest(SLUG) == stored


@pytest.mark.parametrize("producer", ["wiki", "patent"])
def test_stored_v2_when_runtime_lacks_renderer_refuses(producer, monkeypatch) -> None:
    # Given
    original_import = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "automation.interop.owner_message":
            raise ImportError(name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    # When / Then: an existing v2 record is never replayed as v1.
    if producer == "wiki":
        with pytest.raises(wiki_gate.GateError):
            wiki_gate.confirm_text(draft() | {"render_version": 2})
    else:
        with pytest.raises(patent_export_gate.ExportGateError):
            patent_export.render_approval(replace(manifest(), render_version=2))


def test_patent_binding_when_one_wire_line_is_corrupted_refuses() -> None:
    # Given: all other binding lines remain valid.
    from tests.unit.test_wiki_patent_owner_cards import PATENT_V2
    content = PATENT_V2.replace("mode=enc", "mode=plaintext")
    # When
    matches = patent_export_gate.approval_binding_matches(manifest(), content)
    # Then
    assert matches is False
