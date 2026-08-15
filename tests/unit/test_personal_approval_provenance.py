from __future__ import annotations

import json
import re
from pathlib import Path

from automation import skill_gate_specs
from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ApprovalSurface


def test_personal_provenance_when_request_is_recorded_then_head_sha_is_authorized(
    tmp_path: Path,
) -> None:
    # Given
    head = "a" * 40
    provenance_path = tmp_path / "personal-provenance.json"
    _ = provenance_path.write_text(
        json.dumps({"personal_head_sha": head}),
        encoding="utf-8",
    )
    provenance = skill_gate_specs.provenance_of(str(provenance_path))
    spec = skill_gate_specs.DeploySpec(
        skill="demo",
        digest="b" * 64,
        deploy_nonce="c" * 32,
        review_status="- review: PASS",
        provenance=provenance,
        binding=re.compile(r".*", re.DOTALL),
    )
    binding = ApprovalBinding(
        ApprovalKind.SKILL_DEPLOY,
        ApprovalSurface.SKILL_APPROVALS,
        "12345",
        1,
    )

    # When
    record = spec.new_record("approval-message", binding)

    # Then
    assert record["personal_head_sha"] == head
    assert head in spec.render()
    assert spec.action_hash() != skill_gate_specs.DeploySpec(
        skill="demo",
        digest="b" * 64,
        deploy_nonce="c" * 32,
        review_status="- review: PASS",
        provenance=skill_gate_specs.provenance_of(""),
        binding=re.compile(r".*", re.DOTALL),
    ).action_hash()
