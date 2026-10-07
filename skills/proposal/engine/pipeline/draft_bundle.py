from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..contracts.models import EvidenceUnit, FrozenModel, PlanSpec, SectionDraft

if TYPE_CHECKING:
    from ..converter.pms import ProposalMaterialStore


@dataclass(frozen=True)
class _PmsSnapshot(FrozenModel):
    units: list[EvidenceUnit]


def save_planspec(out_path: str, planspec: PlanSpec) -> str:
    path = Path(f"{out_path}.planspec.json")
    _write_json(path, planspec.model_dump(mode="json"))
    return str(path)


def save_drafts(out_path: str, drafts: list[SectionDraft]) -> str:
    return save_draft_file(f"{out_path}.drafts.json", drafts)


def save_draft_file(path: str, drafts: list[SectionDraft]) -> str:
    draft_path = Path(path)
    _write_json(
        draft_path,
        {"sections": [draft.model_dump(mode="json") for draft in drafts]},
    )
    return str(draft_path)


def load_planspec(planspec_path: str) -> PlanSpec:
    return PlanSpec.model_validate_json(Path(planspec_path).read_text(encoding="utf-8"))


def load_drafts(drafts_path: str) -> list[SectionDraft]:
    # Accept legacy list-only bundles as well as the named top-level field used
    # by the standalone ``draft`` command.
    payload = json.loads(Path(drafts_path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        if "sections" not in payload:
            raise ValueError("draft bundle is missing required 'sections' field")
        payload = payload["sections"]
    if not isinstance(payload, list):
        raise ValueError("draft bundle must be a list of sections or carry a 'sections' list")
    return [SectionDraft.model_validate(item) for item in payload]


def load_pms(pms_path: str) -> ProposalMaterialStore:
    from ..converter.pms import ProposalMaterialStore

    snapshot = _PmsSnapshot.model_validate_json(Path(pms_path).read_text(encoding="utf-8"))
    return ProposalMaterialStore(snapshot.units)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "load_drafts",
    "load_planspec",
    "load_pms",
    "save_draft_file",
    "save_drafts",
    "save_planspec",
]
