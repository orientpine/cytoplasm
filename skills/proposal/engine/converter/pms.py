from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ..contracts import CitationStatus, EvidenceUnit
from ..contracts.ids import stable_id


class ProposalMaterialStore:
    def __init__(
        self,
        units: list[EvidenceUnit],
        crossref_verify: Callable[[str], bool] | None = None,
    ) -> None:
        self._crossref_verify: Callable[[str], bool] | None = crossref_verify
        self._by_bucket: dict[str, list[EvidenceUnit]] = defaultdict(list)
        self._by_id: dict[str, EvidenceUnit] = {}
        self._ledger: dict[str, CitationStatus] = {}
        self.ingest(units)

    def ingest(self, units: list[EvidenceUnit]) -> None:
        for unit in sorted(units, key=lambda item: _unit_sort_key(item)):
            self._by_id[unit.unit_id] = unit
            self._ledger[unit.unit_id] = self._citation_status(unit)

        self._rebuild_bucket_index()

    def evidence_for_bucket(self, bucket: str) -> list[EvidenceUnit]:
        return list(self._by_bucket.get(bucket, []))

    def public_evidence(self) -> list[EvidenceUnit]:
        return [
            unit
            for unit in self._ordered_units()
            if self._ledger.get(unit.unit_id) == CitationStatus.PUBLIC
        ]

    def resolve(self, source_id: str) -> EvidenceUnit | None:
        return self._by_id.get(source_id)

    def get_citation_status(self, source_id: str) -> CitationStatus:
        return self._ledger[source_id]

    def promote_to_public(self, unit_id: str) -> bool:
        unit = self._by_id.get(unit_id)
        if unit is None:
            return False

        self._ledger[unit_id] = CitationStatus.PUBLIC
        self._rebuild_bucket_index()
        return True

    def save(self, path: str) -> None:
        store_path = Path(path)
        store_path.mkdir(parents=True, exist_ok=True)

        payload = {
            "units": [unit.model_dump(mode="json") for unit in self._ordered_units()],
            "ledger": self._serialized_ledger(),
        }
        _write_json(store_path / "pms.json", payload)
        _write_json(store_path / "ledger.json", self._serialized_ledger())

    @classmethod
    def load(cls, path: str) -> ProposalMaterialStore:
        store_path = Path(path)
        payload = cast(
            "dict[str, object]",
            json.loads((store_path / "pms.json").read_text(encoding="utf-8")),
        )
        raw_units = cast("list[object]", payload.get("units", []))
        units = [EvidenceUnit.model_validate(item) for item in raw_units]

        store = cls(units)
        ledger_path = store_path / "ledger.json"
        raw_ledger = cast("dict[str, str]", payload.get("ledger", {}))
        if ledger_path.exists():
            raw_ledger = cast(
                "dict[str, str]",
                json.loads(ledger_path.read_text(encoding="utf-8")),
            )
        store._ledger = {
            source_id: CitationStatus(status)
            for source_id, status in sorted(raw_ledger.items(), key=lambda item: _stable_key(item[0]))
        }
        return store

    def _citation_status(self, unit: EvidenceUnit) -> CitationStatus:
        status = CitationStatus.PUBLIC

        if self._crossref_verify is None:
            return status

        refs = [provenance.source_id for provenance in unit.provenances]
        if refs and not all(self._crossref_verify(ref) for ref in refs):
            return CitationStatus.INTERNAL
        return status

    def _ordered_units(self) -> list[EvidenceUnit]:
        return [self._by_id[unit_id] for unit_id in sorted(self._by_id, key=_stable_key)]

    def _rebuild_bucket_index(self) -> None:
        self._by_bucket = defaultdict(list)
        for unit in self._ordered_units():
            self._by_bucket[unit.bucket].append(unit)

    def _serialized_ledger(self) -> dict[str, str]:
        return {
            unit_id: self._ledger[unit_id].value
            for unit_id in sorted(self._ledger, key=_stable_key)
        }


def _unit_sort_key(unit: EvidenceUnit) -> str:
    return _stable_key(unit.unit_id)


def _stable_key(value: str) -> str:
    return stable_id(value)


def _write_json(path: Path, data: object) -> None:
    _ = path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = ["ProposalMaterialStore"]
