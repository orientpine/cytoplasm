"""반출이 끝난 자리에서 스크립트가 릴리스 노트 단계를 이름하는가.

`automation/public_export.sh` 는 `PUBLIC-EXPORT-OK` 한 줄로 끝난다. 그런데 그 순간
사이클은 끝나지 않았다 — GitHub Release 노트가 없으면 신규 설치가 `--update-trust-key`
(필수 인자)에 넣을 `update-trust.pub` 자산도, 번들 키를 **설치기가 아닌 경로로** 대조할
지문 공지도 없다(P0-6 부트스트랩). 규칙은 AGENTS.md 「공개 릴리스 규칙」에 산문으로
있었지만 **실행 시점에는 아무것도 말하지 않아서**, 그 산문을 미리 읽은 주체만 노트를
올렸다 — 2026-09-09 v1.6.7 은 태그만 올라가 목록의 Latest 가 v1.6.1 에 멈춰 있었고,
그 사이 기존 노드는 멀쩡히 업데이트되어 **조용했다**(신규 설치만 막힌다).

**왜 별도 파일인가**: `tests/unit/test_public_export.py` 의 출력 해시는 FS3 정산 레코드
`.omo/evidence/fs3/completions/task-13.json` 에 고정돼 있고 `test_fs3_replay_gate.py` 가
매번 재생을 대조한다. 그 파일에 케이스를 더하면 과거 RED/GREEN 증적이 재현되지 않는다 —
원장을 고쳐 맞추는 것은 증적 위조이므로, 새 검사는 고정되지 않은 이 파일에 둔다
(선례: `test_watch_failure_streak_store.py`, `test_deploy_host_fail_closed_all.py`).

검증은 스크립트를 **실행**해서 한다. 문구 자체는 고정하지 않는다(산문은 바뀌고, 문구를
못박으면 다음 개선이 이 테스트를 고치는 일이 된다) — 소유자가 그대로 실행할 수 있는지를
정하는 것만 본다: 마커, 그 실행의 실제 버전과 대상 저장소, 붙여 쓸 `gh` 명령, 자산 이름.
"""
from __future__ import annotations

from test_public_export import _VERSION, ExportSandbox, _run, export_sandbox

#: 픽스처 재수출. `pytest_plugins` 로는 안 된다 — 원본이 이미 테스트 모듈로 수집되면
#: 플러그인 등록이 건너뛰어져 전량 스위트에서만 `fixture not found` 로 죽는다(실측).
__all__ = ["export_sandbox"]

_MARKER = "PUBLIC-EXPORT-NOTE-PENDING"
_OK = "PUBLIC-EXPORT-OK"
#: `_run` 이 스크립트에 넘기는 값. 안내가 이 실행의 인자에서 채워졌는지 보는 기준점이다.
_REPOSITORY = "local/autophagy-public"


def test_successful_export_names_the_release_note_step(
    export_sandbox: ExportSandbox,
) -> None:
    # Given a clean source and a reachable public stand-in remote.
    # When one export run completes through push and read-back.
    result = _run(export_sandbox)
    assert result.returncode == 0, result.stderr

    # Then the operator is told, at the moment they would otherwise stop, that the
    # cycle is unfinished — and is handed the commands that finish it.
    guidance = result.stderr
    assert _MARKER in guidance, guidance
    assert guidance.index(_OK) < guidance.index(_MARKER), (
        "the pending step must follow the success line, not precede it"
    )

    # The commands must be runnable as printed: this run's version and repository,
    # not a placeholder the operator has to translate.
    assert _VERSION in guidance
    assert _REPOSITORY in guidance
    assert "gh release create" in guidance
    assert "gh release upload" in guidance
    assert "gh release view" in guidance

    # The asset is what a new install needs; the name is the one the install docs promise.
    assert "update-trust.pub" in guidance
    # The private key is never the upload target — the public half is derived.
    assert "ssh-keygen -y" in guidance


def test_blocked_export_does_not_claim_only_the_note_is_left(
    export_sandbox: ExportSandbox,
) -> None:
    # Given an untracked source file, which the dirty-tree precondition refuses.
    _ = (export_sandbox.source / "scratch.txt").write_text(
        "unfinished\n", encoding="utf-8"
    )

    # When export is attempted.
    result = _run(export_sandbox)

    # Then nothing was published, so the note guidance must stay silent — printing it
    # here would read as "only the note is left" on a run that exported nothing.
    assert result.returncode != 0
    assert _MARKER not in result.stderr, result.stderr
