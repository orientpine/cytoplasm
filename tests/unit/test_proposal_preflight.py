"""Proposal resource preflight and shared route-boundary contract tests.

The route-consumer scan intentionally skips files that do not exist yet. Once each future images,
refine, render, or publish consumer lands, this same test immediately requires it to call the one
shared ``assert_route_allowed`` guard; the final F4 verifier requires all four files to exist.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Final, cast

import pytest

from skills.proposal.scripts import proposal_preflight
from skills.proposal.scripts.proposal_route_guard import (
    Destination,
    PayloadKind,
    RouteRefused,
    assert_route_allowed,
)


_REPO: Final = Path(__file__).resolve().parents[2]
ROUTE_CONSUMERS: Final[dict[str, str]] = {
    "images": "skills/proposal/scripts/proposal_images.py",
    "refine": "skills/proposal/scripts/proposal_refine.py",
    "render": "skills/proposal/scripts/proposal_render.py",
    "publish": "skills/proposal/scripts/proposal_publish.py",
}


def _is_guarded_consumer(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id == "assert_route_allowed"
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == "assert_route_allowed"
        )
        for node in ast.walk(tree)
    )


def _route_consumer_failures(consumers: dict[str, str]) -> list[str]:
    return [
        f"{name}: missing assert_route_allowed call"
        for name, relative in consumers.items()
        if (path := _REPO / relative).is_file() and not _is_guarded_consumer(path)
    ]


@pytest.fixture(autouse=True)
def _default_refine_host_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROPOSAL_REFINE_ALLOWED_HOSTS", raising=False)


def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROPOSAL_DOCBOT_ROOT", str(tmp_path / "docbot"))
    monkeypatch.setenv("PROPOSAL_REFINE_ROOT", str(tmp_path / "refine"))
    monkeypatch.setenv("PROPOSAL_IMAGE_API_KEY_ENV", "TEST_PROPOSAL_IMAGE_KEY")
    monkeypatch.delenv("TEST_PROPOSAL_IMAGE_KEY", raising=False)
    monkeypatch.setattr(proposal_preflight.shutil, "which", lambda _name: "/bin/tool")


def test_missing_required_binaries_exit_four_and_list_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolated_environment(monkeypatch, tmp_path)
    missing = {"gws", "pdfimages"}
    monkeypatch.setattr(
        proposal_preflight.shutil,
        "which",
        lambda name: None if name in missing else f"/bin/{name}",
    )

    with pytest.raises(SystemExit) as raised:
        proposal_preflight.main(["--json"])

    assert raised.value.code == 4
    assert "PREFLIGHT-BLOCK: gws, pdfimages" in capsys.readouterr().err


def test_absent_image_key_is_reported_but_only_blocks_images_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolated_environment(monkeypatch, tmp_path)

    assert proposal_preflight.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["checks"]["image-api-key"] == "absent"
    assert report["stages"]["images"] == "blocked"

    with pytest.raises(SystemExit) as raised:
        proposal_preflight.main(["--stage", "images"])
    assert raised.value.code == 4
    assert "PREFLIGHT-BLOCK: images" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("classification", "destination", "host", "payload_kind", "allowed"),
    [
        ("public", "image-api", None, "content", True),
        ("public", "refine-host", None, "content", True),
        ("public", "render", None, "content", True),
        ("public", "drive", None, "content", True),
        ("patent-sensitive", "image-api", None, "content", False),
        ("patent-sensitive", "refine-host", "codex-oauth", "content", True),
        ("patent-sensitive", "render", None, "content", False),
        ("patent-sensitive", "drive", None, "content", True),
        ("owner-private", "image-api", None, "content", False),
        ("owner-private", "refine-host", "codex-oauth", "content", False),
        ("owner-private", "render", None, "content", False),
        ("owner-private", "drive", None, "content", False),
    ],
)
def test_route_guard_full_truth_table(
    classification: str,
    destination: str,
    host: str | None,
    payload_kind: str,
    allowed: bool,
) -> None:
    typed_destination = cast(Destination, destination)
    typed_payload_kind = cast(PayloadKind, payload_kind)
    if allowed:
        decision = assert_route_allowed(
            "bounded payload",
            typed_destination,
            host=host,
            payload_kind=typed_payload_kind,
            classification=classification,
        )
        assert decision.allowed is True
    else:
        with pytest.raises(RouteRefused):
            assert_route_allowed(
                "bounded payload",
                typed_destination,
                host=host,
                payload_kind=typed_payload_kind,
                classification=classification,
            )


@pytest.mark.parametrize(
    "host",
    ["off-tier-host", "other-main", "attacker.example", "public-anthropic-api", None],
)
def test_patent_refine_rejects_hosts_outside_owner_allowlist(host: str | None) -> None:
    with pytest.raises(RouteRefused):
        assert_route_allowed(
            "bounded payload", "refine-host", host=host, classification="patent-sensitive"
        )


def test_public_refine_allows_hosts_outside_owner_allowlist() -> None:
    assert assert_route_allowed(
        "bounded payload", "refine-host", host="attacker.example", classification="public"
    ).allowed


def test_patent_refine_owner_allowlist_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROPOSAL_REFINE_ALLOWED_HOSTS", "node-a, NODE-B ")

    assert assert_route_allowed(
        "bounded payload", "refine-host", host="node-b", classification="patent-sensitive"
    ).allowed
    with pytest.raises(RouteRefused):
        assert_route_allowed(
            "bounded payload",
            "refine-host",
            host="codex-oauth",
            classification="patent-sensitive",
        )


def test_route_guard_uses_source_key_provenance_when_classifying() -> None:
    with pytest.raises(RouteRefused):
        assert_route_allowed(
            "payload without private markers",
            "image-api",
            source_keys=("obsidian:x",),
        )


def _valid_owner_index() -> str:
    return json.dumps(
        [
            {
                "source_key": "obsidian:research/note.md",
                "sha256": "a" * 64,
                "collected_at": "2026-08-23T00:00:00Z",
                "sections": ["approach", "prior-work"],
            }
        ]
    )


def test_owner_private_drive_allows_well_formed_index_payload() -> None:
    decision = assert_route_allowed(
        _valid_owner_index(), "drive", payload_kind="index", classification="owner-private"
    )
    assert decision.allowed is True


@pytest.mark.parametrize(
    "payload",
    [
        "raw personal note bytes",
        json.dumps(
            [
                {
                    "source_key": "obsidian:research/note.md",
                    "sha256": "a" * 64,
                    "sections": ["private prose " + "x" * 2_048],
                }
            ]
        ),
    ],
)
def test_drive_rejects_malformed_or_oversized_index(payload: str) -> None:
    with pytest.raises(RouteRefused, match="^index-shape-invalid$"):
        assert_route_allowed(
            payload, "drive", payload_kind="index", classification="owner-private"
        )


def test_existing_route_consumers_call_the_shared_guard() -> None:
    failures = _route_consumer_failures(ROUTE_CONSUMERS)
    assert not failures, "route consumer failures:\n" + "\n".join(failures)


def test_route_consumer_scanner_requires_a_real_call(tmp_path: Path) -> None:
    direct_call = tmp_path / "direct_call.py"
    direct_call.write_text("assert_route_allowed(payload, 'render')\n", encoding="utf-8")
    attribute_call = tmp_path / "attribute_call.py"
    attribute_call.write_text("guard.assert_route_allowed(payload, 'render')\n", encoding="utf-8")
    prose_only = tmp_path / "prose_only.py"
    prose_only.write_text(
        "\"\"\"assert_route_allowed(payload, 'render')\"\"\"\n"
        "# assert_route_allowed(payload, 'render')\n"
        "render(payload)\n",
        encoding="utf-8",
    )

    assert _is_guarded_consumer(direct_call)
    assert _is_guarded_consumer(attribute_call)
    assert not _is_guarded_consumer(prose_only)


def test_route_consumer_scanner_skips_missing_future_files() -> None:
    assert _route_consumer_failures({"future": "skills/proposal/scripts/not_created_yet.py"}) == []


def test_require_hermes_blocks_when_binary_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolated_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(
        proposal_preflight.shutil,
        "which",
        lambda name: None if name == "hermes" else f"/bin/{name}",
    )

    with pytest.raises(SystemExit) as raised:
        proposal_preflight.main(["--require", "hermes"])

    assert raised.value.code == 4
    assert "PREFLIGHT-BLOCK: hermes" in capsys.readouterr().err
