from __future__ import annotations

from ..contracts import (
    ClaimNode,
    EdgeClaimToEvidence,
    EvidenceGraph,
    EvidenceNode,
    SectionDraft,
)
from ..contracts.ids import stable_id
from .pms import ProposalMaterialStore


def build_evidence_graph(
    pms: ProposalMaterialStore,
    drafts: list[SectionDraft],
) -> EvidenceGraph:
    """Build a deterministic claim→evidence→source knowledge graph.

    Only PUBLIC evidence units appear as nodes.
    """
    public_units = sorted(pms.public_evidence(), key=lambda unit: unit.unit_id)
    public_ids = {unit.unit_id for unit in public_units}
    evidence_nodes = tuple(
        EvidenceNode(source_id=unit.unit_id) for unit in public_units
    )

    claim_nodes: list[ClaimNode] = []
    edges: list[EdgeClaimToEvidence] = []
    for draft in sorted(drafts, key=lambda item: int(item.section_id)):
        for index, claim in enumerate(draft.claims):
            claim_id = stable_id(f"sec{draft.section_id}.claim{index}.{claim.text[:50]}")
            claim_nodes.append(
                ClaimNode(
                    claim_id=claim_id,
                    section_id=draft.section_id,
                    text=claim.text,
                )
            )
            for source_id in sorted(claim.source_ids):
                if source_id in public_ids:
                    edges.append(
                        EdgeClaimToEvidence(
                            claim_id=claim_id,
                            source_id=source_id,
                            relation="supports",
                        )
                    )

    return EvidenceGraph(
        evidence_nodes=evidence_nodes,
        claim_nodes=tuple(sorted(claim_nodes, key=lambda node: (node.section_id, node.claim_id))),
        edges=tuple(sorted(edges, key=lambda edge: (edge.claim_id, edge.source_id))),
    )


def traceability_markdown(evidence_graph: EvidenceGraph) -> str:
    """근거 추적성 관리 문서 — 계획서 본문이 아니라 사이드카 md 로 나간다.

    Coverage 지표는 작성 파이프라인의 내부 품질 게이트다. 심사자가 읽는 문서에
    관리 지표를 실으면 지면만 쓰므로 (소유자 지시 2026-08-28) 아티팩트 옆의
    별도 문서로 관리한다. 정렬 키는 결정성을 위해 (section, claim, source) 고정.
    """
    claim_by_id = {node.claim_id: node for node in evidence_graph.claim_nodes}
    evidence_by_id = {node.source_id: node for node in evidence_graph.evidence_nodes}
    lines = [f"# 근거 추적성 (Coverage Score {evidence_graph.coverage_score:.2f})", ""]
    for edge in sorted(
        evidence_graph.edges,
        key=lambda edge: (
            claim_by_id[edge.claim_id].section_id,
            edge.claim_id,
            edge.source_id,
        ),
    ):
        claim = claim_by_id[edge.claim_id]
        evidence = evidence_by_id[edge.source_id]
        claim_text = claim.text[:30] + ("..." if len(claim.text) > 30 else "")
        lines.append(
            f"- {claim.section_id}절 · {claim_text} · "
            f"{evidence.source_id}"
        )
    return "\n".join(lines) + "\n"


__all__ = ["build_evidence_graph", "traceability_markdown"]
