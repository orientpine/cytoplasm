"""평가용 읽기 경계: 해시 바인딩, 원장 해석, 비공개 루트 거부."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias, cast

from .model import EvalRecord, EvalRecordError, load_record

JSON: TypeAlias = None | bool | int | float | str | list["JSON"] | dict[str, "JSON"]
Groups: TypeAlias = Mapping[str, str | Mapping[str, str]]
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REASONS = frozenset({"timeout", "not-run", "model-missing", "no-artifact", "audio-mismatch",
                      "CANDIDATE-DRIFT", "cli-unavailable", "invalid-artifact", "artifact-mismatch",
                      "cli-failed", "download-failed", "failed", "redacted"})


def private_root(path: Path) -> Path:
    """git 호출 없이 .git 파일/디렉터리 조상을 확인하며 심링크도 해석한다."""
    root = path.expanduser().resolve()
    if any((parent / ".git").exists() for parent in (root, *root.parents)):
        raise EvalRecordError("STT-EVAL-ROOT-REFUSED")
    return root


def digest(value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise EvalRecordError("sha256: 잘못된 해시입니다")
    return value


def jsonl(path: Path) -> list[dict[str, JSON]]:
    if not path.exists():
        return []
    try:
        rows: list[dict[str, JSON]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = cast(JSON, json.loads(line))
            if not isinstance(raw, dict):
                raise EvalRecordError("journal: 객체여야 합니다")
            rows.append(raw)
        return rows
    except (OSError, ValueError) as error:
        raise EvalRecordError("journal: 읽을 수 없는 JSONL입니다") from error


def labels(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in jsonl(root / "candidates.jsonl"):
        label, sha = row.get("label"), digest(row.get("config_sha256"))
        if not isinstance(label, str) or not label:
            raise EvalRecordError("candidate: 라벨이 필요합니다")
        if label in result and result[label] != sha:
            raise EvalRecordError("CANDIDATE-DRIFT")
        result[label] = sha
    return result


def resolve_config(root: Path, value: str) -> str:
    return digest(value) if _SHA.fullmatch(value) else digest(labels(root).get(value))


def configurations(root: Path) -> list[str]:
    return sorted(set(labels(root).values()) | {digest(path.name) for path in (root / "hyp").glob("*") if path.is_dir()})


def records(directory: Path, *, hypothesis: bool = False) -> dict[str, EvalRecord]:
    result: dict[str, EvalRecord] = {}
    for path in sorted(directory.glob("*.json")):
        sha = digest(path.stem)
        record = load_record(path)
        if record.audio_sha256 != sha or hypothesis and record.config_sha256 != digest(directory.name):
            raise EvalRecordError("record: 파일과 레코드 해시가 다릅니다")
        result[sha] = record
    return result


def failures(root: Path, config: str) -> dict[str, str]:
    aliases = {label for label, sha in labels(root).items() if sha == config}
    result: dict[str, str] = {}
    for row in jsonl(root / "runs.jsonl"):
        if row.get("status") != "failed":
            continue
        stored, label = row.get("config_sha256"), row.get("label")
        if stored != config and not (isinstance(label, str) and label in aliases):
            continue
        reason = row.get("reason")
        result[digest(row.get("audio_sha256"))] = reason if isinstance(reason, str) and reason in _REASONS else "redacted"
    return result


def manifest(root: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in jsonl(root / "manifest.jsonl"):
        sha = digest(row.get("audio_sha256"))
        domain, group = row.get("domain", "unknown"), row.get("group_id", sha)
        if domain not in ("meeting", "lifelog", "unknown") or not isinstance(group, str):
            raise EvalRecordError("manifest: 잘못된 도메인/그룹입니다")
        result[sha] = {"domain": str(domain), "group_id": group}
    return result


def metadata(sha: str, groups: Groups, defaults: Groups) -> tuple[str, str]:
    baseline = defaults.get(sha, {})
    domain = baseline.get("domain", "unknown") if isinstance(baseline, Mapping) else "unknown"
    entry = groups.get(sha, baseline)
    if isinstance(entry, str):
        return entry, domain
    group, domain = entry.get("group_id", sha), entry.get("domain", domain)
    if domain not in ("meeting", "lifelog", "unknown"):
        raise EvalRecordError("groups: 잘못된 도메인/그룹입니다")
    return group, domain
