from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.selfskill_audit.store import AuditError, JsonValue, _mapping, _read_json
from automation.skill_review import skill_digest

_SKILL_NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")


@dataclass(frozen=True, slots=True)
class SkillSnapshot:
    name: str
    sha256: str
    provenance: str
    pinned: bool
    archived_at: str | None


def _governed_names(governed_root: Path | None) -> frozenset[str]:
    """governed 루트의 스킬 이름 — entry 이름만 읽으면 된다(심링크를 따라가지 않는다).

    Hermes 자신의 충돌 검사는 `rglob("SKILL.md")` 라 심링크 팜인 이 루트를 못 본다
    (2026-08-16 실측: `_find_skill("recall")` → None). 그래서 자가 스킬이 배포본 이름을
    선점해 **승인 게이트를 강제하는 구현을 가릴 수 있다** — 발견은 1차 루트가 이기므로.
    벤더 쪽을 고칠 수 없으니 최소한 소유자에게 즉시 보이게 한다.
    """
    if governed_root is None or not governed_root.is_dir():
        return frozenset()
    try:
        return frozenset(p.name for p in governed_root.iterdir() if not p.name.startswith("."))
    except OSError as error:
        raise AuditError(f"cannot read governed skill root: {governed_root}") from error


def _usage(skills_root: Path) -> dict[str, JsonValue]:
    # 부재는 정상이다 — 반전 직후의 새 1차 루트에는 Hermes가 아직 상태를 쓰지 않았다.
    # 손상은 정상이 아니다: 읽히지 않거나 형태가 틀리면 그대로 실패한다(fail-closed).
    usage_path = skills_root / ".usage.json"
    if not usage_path.exists():
        return {}
    raw = _mapping(_read_json(usage_path, "skill usage"), "skill usage")
    nested = raw.get("skills")
    return _mapping(nested, "skill usage.skills") if nested is not None else raw


def _bundled_names(skills_root: Path) -> frozenset[str]:
    """Hermes 가 시드한 번들 스킬 이름 — 이 원장의 대상이 아니다.

    1차 루트가 쓰기 가능해지자 Hermes 가 자기 번들 카탈로그를 그 루트에 시드했다
    (2026-08-16 실측: 반전 직후 재기동에서 68종). 벤더 스킬을 "에이전트가 만들었다"고
    보고하면 이 원장이 나르려는 단 하나의 신호가 묻힌다. 부재는 정상(빈 집합),
    읽을 수 없으면 구분할 수 없으므로 멈춘다(fail-closed).
    """
    manifest = skills_root / ".bundled_manifest"
    if not manifest.exists():
        return frozenset()
    try:
        document = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise AuditError(f"cannot read trusted bundled manifest: {manifest}") from error
    return frozenset(line.split(":", 1)[0].strip() for line in document.splitlines() if line.strip())


def _snapshot(skill_dir: Path, usage: dict[str, JsonValue]) -> SkillSnapshot:
    name = skill_dir.name
    if _SKILL_NAME.fullmatch(name) is None:
        raise AuditError(f"unsafe skill name: {name!r}")
    raw_record = usage.get(name, {})
    record = _mapping(raw_record, f"usage record for {name}")
    created_by = record.get("created_by")
    agent_created = record.get("agent_created")
    pinned = record.get("pinned", False)
    archived_at = record.get("archived_at")
    if created_by is not None and not isinstance(created_by, str):
        raise AuditError(f"created_by is malformed for {name}")
    if agent_created is not None and not isinstance(agent_created, bool):
        raise AuditError(f"agent_created is malformed for {name}")
    if not isinstance(pinned, bool):
        raise AuditError(f"pinned is malformed for {name}")
    if archived_at is not None and not isinstance(archived_at, str):
        raise AuditError(f"archived_at is malformed for {name}")
    provenance = "agent" if created_by == "agent" or agent_created is True else "unverified"
    try:
        digest = skill_digest(skill_dir)
    except OSError as error:
        raise AuditError(f"cannot hash skill: {name}") from error
    return SkillSnapshot(name, digest, provenance, pinned, archived_at)


def _children(root: Path) -> tuple[Path, ...]:
    try:
        return tuple(sorted(root.iterdir(), key=lambda path: os.fsencode(path.name)))
    except OSError as error:
        raise AuditError(f"cannot scan skills root: {root}") from error


def _is_skill_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink() and (path / "SKILL.md").is_file()


def _skill_dirs(root: Path) -> tuple[Path, ...]:
    # Hermes는 자가 저작 스킬을 카테고리 디렉터리 아래에 둔다 — peer 실측
    # `software-development/<name>/SKILL.md`. 최상위만 훑으면 원장이 영구히 0건을 보고한다.
    # 그래서 깊이 1과 2를 함께 훑되, `.archive`/`.hub` 같은 Hermes 자체 상태는 건너뛴다
    # (아카이브는 호출자가 루트로 직접 넘겨주므로 그때는 그 자식들이 대상이 된다).
    found: list[Path] = []
    for path in _children(root):
        if path.name.startswith("."):
            continue
        if _is_skill_dir(path):
            found.append(path)
            continue
        if path.is_dir() and not path.is_symlink():
            found.extend(nested for nested in _children(path) if _is_skill_dir(nested))
    return tuple(found)


def _scan(root: Path, usage: dict[str, JsonValue], bundled: frozenset[str]) -> tuple[SkillSnapshot, ...]:
    return tuple(
        _snapshot(skill_dir, usage)
        for skill_dir in _skill_dirs(root)
        if skill_dir.name not in bundled
    )


def shadowed_skill_names(home: Path, governed_root: Path | None) -> tuple[str, ...]:
    """이름 대조만 — 2분 틱용 저비용 SHADOWS 검사(해시·원장·usage 무접촉, SC-1).

    일 1회 감사(`ledger.audit`)와 판정 기준이 갈라지면 한쪽만 보는 그림자가 생기므로
    같은 walk(`_skill_dirs`)·같은 번들 제외·같은 governed 이름 읽기를 재사용한다.
    """
    skills_root = home / ".hermes" / "skills"
    if not skills_root.is_dir():
        return ()
    governed = _governed_names(governed_root)
    if not governed:
        return ()
    bundled = _bundled_names(skills_root)
    names = {skill_dir.name for skill_dir in _skill_dirs(skills_root)}
    return tuple(sorted((names - bundled) & governed))


def _parse_snapshot(value: JsonValue, name: str) -> SkillSnapshot:
    record = _mapping(value, f"stored snapshot for {name}")
    sha256 = record.get("sha256")
    provenance = record.get("provenance")
    pinned = record.get("pinned")
    archived_at = record.get("archived_at")
    if not isinstance(sha256, str) or not isinstance(provenance, str) or not isinstance(pinned, bool):
        raise AuditError(f"stored snapshot is malformed for {name}")
    if archived_at is not None and not isinstance(archived_at, str):
        raise AuditError(f"stored archive timestamp is malformed for {name}")
    return SkillSnapshot(name, sha256, provenance, pinned, archived_at)
