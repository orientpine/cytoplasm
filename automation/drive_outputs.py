"""Single taxonomy-aware facade for publishing final artifacts to Google Drive.

All Drive mutations and verification go through :class:`DriveClient`. Callers
that require durable Drive storage use :func:`publish`; optional skill hooks use
:func:`publish_best_effort`, which is disabled unless explicitly opted in.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, TypeAlias

from automation.drive_client import DriveClient
from automation.drive_taxonomy import (
    artifact_name,
    bundle_name,
    category,
    TaxonomyError,
    ensure_depth,
    folder_parts,
    period_key,
)

_DEFAULT_CACHE: Final = Path.home() / ".hermes" / "drive-publish" / "folders.json"
_FOLDER_MIME: Final = "application/vnd.google-apps.folder"
_YEAR_NAME: Final = re.compile(r"^\d{4}$")

Artifact: TypeAlias = tuple[Path, str]


@dataclass(frozen=True, slots=True)
class PublishResult:
    links: tuple[str, ...]
    action: str
    folder_id: str


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def client_from_environment() -> DriveClient:
    """Build the production client using the shared publisher configuration."""
    gws_bin = (
        os.environ.get("DRIVE_GWS_BIN")
        or os.environ.get("DRIVE_PUBLISH_GWS_BIN")
        or "gws"
    )
    configured_cache = os.environ.get("DRIVE_PUBLISH_CACHE")
    cache = Path(configured_cache).expanduser() if configured_cache else _DEFAULT_CACHE
    return DriveClient(gws_bin=gws_bin, folder_cache=cache)


def _validate_inputs(
    title: str,
    artifacts: Sequence[Artifact],
    companions: Sequence[Path],
) -> tuple[str, tuple[Artifact, ...], tuple[Path, ...]]:
    normalized_title = _nfc(title)
    if not normalized_title:
        raise ValueError("publish title is empty")
    if not artifacts:
        raise ValueError("publish requires at least one artifact")

    checked_artifacts: list[Artifact] = []
    for local, artifact_title in artifacts:
        path = Path(local)
        if not path.is_file():
            raise FileNotFoundError(path)
        normalized_artifact_title = _nfc(artifact_title)
        if not normalized_artifact_title:
            raise ValueError("artifact title is empty")
        checked_artifacts.append((path, normalized_artifact_title))

    checked_companions: list[Path] = []
    for local in companions:
        path = Path(local)
        if not path.is_file():
            raise FileNotFoundError(path)
        checked_companions.append(path)

    return normalized_title, tuple(checked_artifacts), tuple(checked_companions)


def _sticky_period(
    client: DriveClient,
    category_folder_id: str,
    title: str,
) -> str | None:
    """Find a one-shot's original day by scanning numeric years newest-first."""
    years: list[tuple[int, str]] = []
    for child in client.list_children(category_folder_id):
        name = _nfc(str(child.get("name", "")))
        folder_id = str(child.get("id", ""))
        mime_type = str(child.get("mimeType", ""))
        if _YEAR_NAME.fullmatch(name) and mime_type == _FOLDER_MIME and folder_id:
            years.append((int(name), folder_id))

    target = re.compile(
        rf"^(\d{{4}}-\d{{2}}-\d{{2}})_{re.escape(title)}(?:\.[^/]*)?$"
    )
    for _, year_folder_id in sorted(years, reverse=True):
        for child in client.list_children(year_folder_id):
            name = _nfc(str(child.get("name", "")))
            match = target.fullmatch(name)
            if match is not None:
                return match.group(1)
    return None


def publish(
    kind: str,
    title: str,
    artifacts: Sequence[Artifact],
    *,
    companions: Sequence[Path] = (),
    on: date | None = None,
    project: str | None = None,
    client: DriveClient | None = None,
) -> PublishResult:
    """Upsert and verify one logical output in the canonical Drive tree.

    One-shot documents retain the date prefix from their first publication.
    Every uploaded file is owner-only checked and read back for sha256 equality
    before this function returns.
    """
    selected = category(kind)
    # folder_parts is the registry's gate-only enforcement boundary. Resolve it
    # before validating local paths so a forbidden kind can never reach Drive.
    requested_on = on or date.today()
    initial_parts = folder_parts(kind, requested_on.year, project=project)
    normalized_title, checked_artifacts, checked_companions = _validate_inputs(
        title, artifacts, companions
    )
    drive = client or client_from_environment()

    period = period_key(selected.periodicity, requested_on)
    if selected.periodicity == "oneshot":
        # Everything but the year: the folder whose year children hold prior copies.
        # With a project that is the project folder, so a re-publish keeps its original
        # date instead of starting a second copy under today's.
        scan_parts = ensure_depth(initial_parts[:-1])
        scan_folder_id = drive.ensure_folder_path(scan_parts)
        sticky = _sticky_period(drive, scan_folder_id, normalized_title)
        if sticky is not None:
            period = sticky

    # Period keys always begin with their governing year. For ISO weeks this
    # can intentionally differ from the input date's calendar year.
    target_year = int(period[:4])
    base_parts = folder_parts(kind, target_year, project=project)
    bundled = selected.always_bundle or len(checked_artifacts) + len(checked_companions) > 1
    if bundled:
        parent_parts = ensure_depth((*base_parts, bundle_name(period, normalized_title)))
    else:
        parent_parts = ensure_depth(base_parts)
    parent_id = drive.ensure_folder_path(parent_parts)

    uploads: list[tuple[Path, str]] = []
    for local, artifact_title in checked_artifacts:
        name = artifact_name(period, artifact_title, local.suffix)
        _ = ensure_depth((*parent_parts, name))
        uploads.append((local, name))
    for local in checked_companions:
        name = _nfc(local.name)
        _ = ensure_depth((*parent_parts, name))
        uploads.append((local, name))

    links: list[str] = []
    actions: list[str] = []
    for local, name in uploads:
        result = drive.upsert_file(local, name, parent_id)
        file_id = result["id"]
        drive.verify_owner_only(file_id)
        _ = drive.download_and_verify(file_id, local)
        links.append(result["webViewLink"])
        actions.append(result["action"])

    action = "updated" if actions and all(item == "updated" for item in actions) else "created"
    return PublishResult(tuple(links), action, parent_id)


def publish_state_file(
    parts: Sequence[str],
    name: str,
    local: Path,
    *,
    client: DriveClient | None = None,
) -> str:
    """Upsert and verify one non-dated state file in its exact folder path."""
    normalized_name = _nfc(name)
    if not normalized_name or "/" in normalized_name or "\\" in normalized_name:
        raise TaxonomyError(f"invalid state file name: {name!r}")
    checked = ensure_depth((*parts, normalized_name))
    drive = client or client_from_environment()
    parent_id = drive.ensure_folder_path(checked[:-1])
    result = drive.upsert_file(local, normalized_name, parent_id)
    file_id = result["id"]
    drive.verify_owner_only(file_id)
    _ = drive.download_and_verify(file_id, local)
    return result["webViewLink"]


def fetch_state_file(
    parts: Sequence[str],
    name: str,
    dest: Path,
    *,
    client: DriveClient | None = None,
) -> bool:
    """Download one named non-dated state file when it exists in ``parts``."""
    drive = client or client_from_environment()
    parent_id = drive.ensure_folder_path(tuple(parts))
    normalized_name = _nfc(name)
    for child in drive.list_children(parent_id):
        if _nfc(str(child.get("name", ""))) != normalized_name:
            continue
        file_id = str(child.get("id", ""))
        if file_id:
            _ = drive.download_file(file_id, dest)
            return True
    return False


def publish_best_effort(
    kind: str,
    title: str,
    artifacts: Sequence[Artifact],
    *,
    companions: Sequence[Path] = (),
    on: date | None = None,
    project: str | None = None,
    client: DriveClient | None = None,
) -> PublishResult | None:
    """Publish only when opted in and reduce any failure to one safe marker."""
    if os.environ.get("DRIVE_PUBLISH_ENABLED") != "1":
        return None
    try:
        return publish(
            kind,
            title,
            artifacts,
            companions=companions,
            on=on,
            project=project,
            client=client,
        )
    except Exception as error:
        safe_kind = str(kind).replace("\r", "_").replace("\n", "_")
        print(
            f"DRIVE-PUBLISH-FAIL kind={safe_kind} reason={type(error).__name__}",
            file=sys.stderr,
        )
        return None
