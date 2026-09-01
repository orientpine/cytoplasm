"""Verified, resumable publication of every proposal version artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from skills.proposal.scripts import proposal_publish


def _tree(tmp_path: Path, *, raw: bool = False) -> tuple[Path, Path]:
    root = tmp_path / "proposals"
    version = root / "demo" / "versions" / "v000001"
    (version / "inputs").mkdir(parents=True)
    (version / "out").mkdir()
    (version / "inputs" / "RESEARCH_BRIEF.md").write_text("brief\n", encoding="utf-8")
    (version / "out" / "proposal.hwpx").write_bytes(b"hwpx bytes")
    (version / "figures.json").write_text("[]\n", encoding="utf-8")
    (version / "manifest.json").write_text(
        '{"schema_version":1,"version":"v000001"}\n', encoding="utf-8"
    )
    if raw:
        (version / "delta" / "raw").mkdir(parents=True)
        (version / "delta" / "raw" / "private.md").write_bytes(b"PRIVATE-SENTINEL-BYTES")
        (version / "delta" / "INDEX.json").write_text(
            json.dumps(
                [
                    {
                        "source_key": "obsidian:proposal/private.md",
                        "sha256": "a" * 64,
                        "collected_at": "2026-08-23T00:00:00Z",
                        "sections": ["approach"],
                    }
                ]
            ),
            encoding="utf-8",
        )
    return root, version


def test_script_entrypoint_can_import_live_drive_client_outside_repo(tmp_path: Path) -> None:
    root, _version = _tree(tmp_path)
    cli = Path(__file__).resolve().parents[2] / "skills/proposal/scripts/proposal_cli.py"
    env = {
        **os.environ,
        "DRIVE_GWS_BIN": "/bin/false",
        "PROPOSAL_ROOT": str(root),
        "PYTHONPATH": "",
    }

    completed = subprocess.run(
        [sys.executable, str(cli), "publish", "--slug", "demo", "--version", "v000001"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "PROPOSAL-PUBLISH-REFUSED" in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr


def _publish(root: Path, transport: proposal_publish.FakeDriveTransport) -> proposal_publish.PublishResult:
    return proposal_publish.publish_version(root, "demo", "v000001", transport=transport)


def test_draft_preview_manifest_is_refused_before_transport_or_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, version = _tree(tmp_path)
    manifest_path = version / "manifest.json"
    manifest_path.write_text(
        '{"draft_preview":true,"schema_version":1,"version":"v000001"}\n',
        encoding="utf-8",
    )
    original_manifest = manifest_path.read_bytes()
    fake_state = tmp_path / "fake-state.json"
    fake = proposal_publish.FakeDriveTransport(fake_state)
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setattr(proposal_publish, "_transport_from_environment", lambda _root: fake)

    return_code = proposal_publish.command(
        argparse.Namespace(slug="demo", version="v000001", json=False)
    )

    assert return_code == 5
    assert "draft_preview: true" in capsys.readouterr().err
    assert fake.calls == []
    assert manifest_path.read_bytes() == original_manifest
    assert not fake_state.exists()
    assert not (version / "publish-receipt.json").exists()
    assert not (root / "demo" / ".publish-state" / "v000001.json").exists()


@pytest.mark.parametrize("draft_preview", [False, None])
def test_non_draft_preview_manifests_remain_publishable(
    tmp_path: Path, draft_preview: bool | None
) -> None:
    root, version = _tree(tmp_path)
    manifest_path = version / "manifest.json"
    manifest = {"schema_version": 1, "version": "v000001"}
    if draft_preview is not None:
        manifest["draft_preview"] = draft_preview
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    fake = proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json")

    result = _publish(root, fake)

    assert result.receipt.is_file()
    assert any(operation == "upsert_file" for operation, _path in fake.calls)


def test_each_upload_uses_verified_drive_order(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    fake = proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json")

    result = _publish(root, fake)

    assert result.uploads
    for uploaded in result.uploads:
        calls = [call[0] for call in fake.calls if call[1] == uploaded.path]
        assert calls == [
            "ensure_folder_path",
            "upsert_file",
            "verify_owner_only",
            "download_and_verify",
        ]


def test_owner_only_violation_stops_before_further_uploads(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    fake = proposal_publish.FakeDriveTransport(
        tmp_path / "fake-state.json", failure="public-perm"
    )

    with pytest.raises(proposal_publish.PublishPermissionError):
        _publish(root, fake)

    assert sum(call[0] == "upsert_file" for call in fake.calls) == 1
    assert not any(call[0] == "download_and_verify" for call in fake.calls)


def test_sha_mismatch_fails_closed(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    fake = proposal_publish.FakeDriveTransport(
        tmp_path / "fake-state.json", failure="sha-mismatch"
    )

    with pytest.raises(proposal_publish.PublishShaMismatch):
        _publish(root, fake)


def test_completed_rerun_has_zero_upserts(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    fake = proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json")
    first = _publish(root, fake)
    assert first.uploads
    fake.calls.clear()

    second = _publish(root, fake)

    assert second.uploads == ()
    assert not any(call[0] == "upsert_file" for call in fake.calls)


def test_manifest_and_receipt_have_non_recursive_inventory(tmp_path: Path) -> None:
    root, version = _tree(tmp_path)
    fake = proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json")

    _publish(root, fake)

    manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    inventory = manifest["files"]
    assert set(inventory) == {
        "figures.json",
        "inputs/RESEARCH_BRIEF.md",
        "out/proposal.hwpx",
    }
    assert all(record["id"] and len(record["sha256"]) == 64 for record in inventory.values())
    assert "manifest.json" not in inventory
    assert "publish-receipt.json" not in inventory

    receipt = json.loads((version / "publish-receipt.json").read_text(encoding="utf-8"))
    manifest_upload = fake.file_for_path("manifest.json")
    assert set(receipt) == {"manifest"}
    assert set(receipt["manifest"]) == {"id", "sha256"}
    assert receipt["manifest"] == {
        "id": manifest_upload["id"],
        "sha256": hashlib.sha256((version / "manifest.json").read_bytes()).hexdigest(),
    }


def test_index_requires_all_boundary_keys(tmp_path: Path) -> None:
    root, version = _tree(tmp_path, raw=True)
    index = version / "delta" / "INDEX.json"
    index.write_text(
        json.dumps([{"source_key": "obsidian:x", "sha256": "a" * 64, "sections": []}]),
        encoding="utf-8",
    )
    with pytest.raises(proposal_publish.PublishBoundaryError, match="delta/INDEX.json"):
        _publish(root, proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json"))

    index.write_text(
        json.dumps(
            [
                {
                    "source_key": "obsidian:x",
                    "sha256": "a" * 64,
                    "collected_at": "2026-08-23T00:00:00Z",
                    "sections": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    _publish(root, proposal_publish.FakeDriveTransport(tmp_path / "complete-state.json"))


def test_unexpected_files_are_traversed(tmp_path: Path) -> None:
    root, version = _tree(tmp_path)
    extra = version / "out" / "extra-sidecar.bin"
    extra.write_bytes(b"unexpected sidecar bytes")
    fake = proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json")

    _publish(root, fake)

    assert "out/extra-sidecar.bin" in [path for operation, path in fake.calls if operation == "upsert_file"]


def test_private_raw_bytes_are_replaced_by_index(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path, raw=True)
    fake = proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json")

    _publish(root, fake)

    assert fake.uploaded_bytes("delta/raw/private.md") is None
    assert b"PRIVATE-SENTINEL-BYTES" not in fake.all_uploaded_bytes()
    assert fake.uploaded_bytes("delta/INDEX.json") is not None


def test_raw_boundary_requires_index(tmp_path: Path) -> None:
    root, version = _tree(tmp_path)
    (version / "delta" / "raw").mkdir(parents=True)
    (version / "delta" / "raw" / "private.md").write_text("private", encoding="utf-8")
    fake = proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json")

    with pytest.raises(proposal_publish.PublishBoundaryError, match="delta/INDEX.json"):
        _publish(root, fake)

    assert not fake.calls


def test_receipt_failure_resume_refinalizes_without_stale_receipt_inventory(
    tmp_path: Path,
) -> None:
    root, version = _tree(tmp_path)
    state = tmp_path / "fake-state.json"
    failing = proposal_publish.FakeDriveTransport(state, failure="receipt")

    with pytest.raises(proposal_publish.PublishError, match="receipt"):
        _publish(root, failing)
    stale_receipt = (version / "publish-receipt.json").read_bytes()
    assert failing.uploaded_bytes("publish-receipt.json") is None

    resumed = proposal_publish.FakeDriveTransport(state)
    result = _publish(root, resumed)

    assert any(upload.path == "manifest.json" for upload in result.uploads)
    assert any(upload.path == "publish-receipt.json" for upload in result.uploads)
    assert stale_receipt
    manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    assert "publish-receipt.json" not in manifest["files"]
    receipt = json.loads((version / "publish-receipt.json").read_text(encoding="utf-8"))
    assert receipt["manifest"]["sha256"] == hashlib.sha256(
        (version / "manifest.json").read_bytes()
    ).hexdigest()
    upsert_paths = [call[1] for call in resumed.calls if call[0] == "upsert_file"]
    assert upsert_paths[-1] == "publish-receipt.json"
    assert upsert_paths.count("publish-receipt.json") == 1


def test_mid_tree_interruption_resumes_after_verified_files(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    state = tmp_path / "fake-state.json"
    interrupted = proposal_publish.FakeDriveTransport(state, failure="mid-tree")

    with pytest.raises(proposal_publish.PublishError, match="mid-tree"):
        _publish(root, interrupted)

    resumed = proposal_publish.FakeDriveTransport(state)
    _publish(root, resumed)
    upserted = [path for operation, path in resumed.calls if operation == "upsert_file"]
    assert "figures.json" not in upserted


def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    root, version = _tree(tmp_path)
    (version / "linked").symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(proposal_publish.PublishBoundaryError, match="symlink"):
        _publish(root, proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json"))

def test_publish_root_follows_the_shared_outputs_root(tmp_path: Path, monkeypatch) -> None:
    # Given: the shared Drive root is overridden the way drive_taxonomy honours it.
    root, _ = _tree(tmp_path)
    fake = proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json")
    seen: list[tuple[str, ...]] = []
    resolve = fake.ensure_folder_path
    monkeypatch.setattr(
        fake, "ensure_folder_path", lambda parts: (seen.append(tuple(parts)), resolve(parts))[1]
    )
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", "다른루트")

    # When: a version tree is published.
    _publish(root, fake)

    # Then: proposal writes under the same root as every other skill — a rename that
    # moves everything else must not leave this one skill behind.
    assert seen, "no folder was resolved"
    assert {parts[0] for parts in seen} == {"다른루트"}


def test_publish_root_defaults_to_the_shared_default(tmp_path: Path, monkeypatch) -> None:
    root, _ = _tree(tmp_path)
    fake = proposal_publish.FakeDriveTransport(tmp_path / "fake-state.json")
    seen: list[tuple[str, ...]] = []
    resolve = fake.ensure_folder_path
    monkeypatch.setattr(
        fake, "ensure_folder_path", lambda parts: (seen.append(tuple(parts)), resolve(parts))[1]
    )
    monkeypatch.delenv("DRIVE_OUTPUTS_ROOT", raising=False)

    _publish(root, fake)

    assert {parts[0] for parts in seen} == {"autophagy"}
