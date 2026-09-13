"""Mail attachment manifest policy extracted without behavior changes."""
from __future__ import annotations
import hashlib
import json
import mimetypes
from pathlib import Path

MAX_ATTACHMENT_COUNT = 10

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

MAX_ATTACHMENT_TOTAL_BYTES = 25 * 1024 * 1024

BLOCKED_ATTACHMENT_SUFFIXES = frozenset(
    {".bat", ".cmd", ".com", ".exe", ".js", ".msi", ".ps1", ".scr"}
)

class AttachmentPolicyError(ValueError):
    """Stable, path-safe attachment validation failure."""

    def __init__(self, message: str, error_code: str = "attachment_invalid") -> None:
        super().__init__(message)
        self.error_code = error_code

def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def build_attachment_manifest(paths: tuple[str | Path, ...]) -> list[dict]:
    """Validate private local files and return an approval-bound manifest.

    File content and source paths remain in the mode-700 draft store. Only the
    safe display metadata is rendered to Discord or audit surfaces.
    """
    if len(paths) > MAX_ATTACHMENT_COUNT:
        raise AttachmentPolicyError(
            f"첨부는 최대 {MAX_ATTACHMENT_COUNT}개까지 지원합니다",
            "attachment_unsupported",
        )
    manifest: list[dict] = []
    total = 0
    for raw_path in paths:
        try:
            path = Path(raw_path).expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError) as error:
            raise AttachmentPolicyError("첨부파일을 읽을 수 없습니다") from error
        if not path.is_file():
            raise AttachmentPolicyError("첨부 대상이 일반 파일이 아닙니다")
        display_name = path.name
        if not display_name or any(ord(char) < 32 for char in display_name):
            raise AttachmentPolicyError("첨부파일 이름이 올바르지 않습니다")
        if path.suffix.lower() in BLOCKED_ATTACHMENT_SUFFIXES:
            raise AttachmentPolicyError(
                "보안 정책상 지원하지 않는 첨부파일 형식입니다",
                "attachment_unsupported",
            )
        try:
            size_bytes = path.stat().st_size
        except OSError as error:
            raise AttachmentPolicyError("첨부파일을 읽을 수 없습니다") from error
        if size_bytes > MAX_ATTACHMENT_BYTES:
            raise AttachmentPolicyError(
                f"첨부파일 한 개의 최대 크기는 {MAX_ATTACHMENT_BYTES}바이트입니다",
                "attachment_unsupported",
            )
        total += size_bytes
        if total > MAX_ATTACHMENT_TOTAL_BYTES:
            raise AttachmentPolicyError(
                f"전체 첨부파일의 최대 크기는 {MAX_ATTACHMENT_TOTAL_BYTES}바이트입니다",
                "attachment_unsupported",
            )
        try:
            content_sha256 = _file_sha256(path)
        except OSError as error:
            raise AttachmentPolicyError("첨부파일을 읽을 수 없습니다") from error
        mime_type = mimetypes.guess_type(display_name, strict=False)[0] or "application/octet-stream"
        manifest.append(
            {
                "source_path_private": str(path),
                "display_name": display_name,
                "size_bytes": size_bytes,
                "mime_type": mime_type,
                "sha256": content_sha256,
            }
        )
    return manifest

def attachment_manifest_sha256(manifest: list[dict]) -> str:
    """Digest upload-relevant metadata without exposing the private source path."""
    public_manifest = [
        {
            "display_name": item["display_name"],
            "size_bytes": item["size_bytes"],
            "mime_type": item["mime_type"],
            "sha256": item["sha256"],
        }
        for item in manifest
    ]
    canonical = json.dumps(
        public_manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def verify_attachment_manifest(manifest: list[dict], expected_sha256: str) -> None:
    """Re-read approved files immediately before upload and fail on any drift."""
    try:
        current = build_attachment_manifest(
            tuple(item["source_path_private"] for item in manifest)
        )
    except (KeyError, TypeError) as error:
        raise AttachmentPolicyError("첨부 manifest 형식이 올바르지 않습니다") from error
    if current != manifest or attachment_manifest_sha256(current) != expected_sha256:
        raise AttachmentPolicyError("승인 후 첨부파일이 변경되어 발송을 중단했습니다")
