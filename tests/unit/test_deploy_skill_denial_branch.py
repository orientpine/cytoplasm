"""A cancelled deploy and an unanswered one are different events, and must read that way.

FA-2 precondition. `deploy-skill.sh` had one branch for "not approved", so pressing ⛔
produced the same outcome as not having answered yet: a peer-attestation refresh was
spent re-attesting a deployment that is not happening, and the operator was told
"approval ABSENT or INVALID" — which is not what occurred. The owner answered. The
answer was no.

The split matters beyond the wording. FA-2's resume path has to retain the reviewed
artifact for a request still awaiting an answer and discard it for a refused one, and
FA-3's watcher has to retain the former and retire the latter. One exit code cannot
drive either decision.

`refresh_gate_check` must stay reachable only from the unanswered case. Refreshing a peer
attestation for a cancelled deploy is a Discord round-trip spent on nothing, and worse,
it makes the logs read as though the system were still trying to get the deploy through
after the owner stopped it.
"""
from __future__ import annotations

from pathlib import Path

from automation.skill_gate import DENIED_EXIT

_DEPLOY = Path(__file__).resolve().parents[2] / "automation" / "deploy-skill.sh"
_SOURCE = _DEPLOY.read_text(encoding="utf-8")


def _index(needle: str) -> int:
    position = _SOURCE.find(needle)
    assert position != -1, f"missing from deploy-skill.sh: {needle!r}"
    return position


def test_the_denied_exit_code_has_its_own_branch() -> None:
    assert f'"$APPROVED" == {DENIED_EXIT}' in _SOURCE


def test_the_denied_branch_says_cancelled_not_absent() -> None:
    """The operator must not be told the owner failed to answer when they refused."""
    branch_start = _index(f'"$APPROVED" == {DENIED_EXIT}')
    absent_branch = _index('"$APPROVED" != 0')
    assert branch_start < absent_branch, "the specific case must be tested first"
    between = _SOURCE[branch_start:absent_branch]
    assert "CANCELLED" in between
    assert "ABSENT" not in between


def test_the_attestation_refresh_is_reachable_only_from_the_unanswered_case() -> None:
    """Refreshing an attestation for a cancelled deploy spends a round-trip on nothing."""
    refresh = _index("refresh_gate_check --skill")
    guard = _SOURCE.rfind('"$approved" == 1', 0, refresh)
    assert guard != -1, "refresh_gate_check must sit under the exit-1 guard"
    assert f'"$approved" == {DENIED_EXIT}' not in _SOURCE[guard:refresh]


def test_a_cancelled_deploy_does_not_exit_zero() -> None:
    branch_start = _index(f'"$APPROVED" == {DENIED_EXIT}')
    absent_branch = _index('"$APPROVED" != 0')
    between = _SOURCE[branch_start:absent_branch]
    assert "exit 0" not in between


def test_the_denied_branch_cleans_up_like_the_absent_one() -> None:
    """Staging left behind by a refused deploy is drift nobody will ever come back for."""
    branch_start = _index(f'"$APPROVED" == {DENIED_EXIT}')
    absent_branch = _index('"$APPROVED" != 0')
    between = _SOURCE[branch_start:absent_branch]
    assert "cleanup_review_staging" in between
    assert "cleanup_e2e_injection" in between


def test_review_staging_cleanup_unseals_the_tree_before_removing_it() -> None:
    function_start = _index("cleanup_review_staging() {")
    function_end = _SOURCE.index("\n}", function_start)
    body = _SOURCE[function_start:function_end]

    chmod = body.find("chmod -R u+w")
    remove = body.find("rm -rf")

    assert chmod != -1
    assert remove != -1
    assert chmod < remove


def test_gate_configuration_error_is_not_collapsed_into_approval_absence() -> None:
    config_branch = _index('"$APPROVED" == 2')
    absent_branch = _index('"$APPROVED" != 0')
    between = _SOURCE[config_branch:absent_branch]

    assert config_branch < absent_branch
    assert "exit 4" in between


def test_a_cancelled_deploy_does_not_reuse_the_lease_contention_code() -> None:
    """exit 8 already means "another execution holds this skill's lease".

    The two need OPPOSITE handling from a caller: lease contention is transient and
    must be retried, while a cancellation is the owner's decision and must retire the
    request. A watcher reading one number for both would destroy a live approval that
    had merely collided with a concurrent deploy.
    """
    branch_start = _index(f'"$APPROVED" == {DENIED_EXIT}')
    absent_branch = _index('"$APPROVED" != 0')
    between = _SOURCE[branch_start:absent_branch]
    assert "exit 8" not in between, "8 is EXECUTION-LOCK-BLOCK"
    assert "exit 9" in between


def test_the_exit_code_table_documents_the_cancellation_code() -> None:
    """An undocumented outgoing code is one a caller has to guess at."""
    header = _SOURCE[: _index("set -euo pipefail")]
    assert "9 owner cancelled" in header
    assert "8 another execution holds" in header
