"""제작 전 기존 스킬 대조(t_0a7959e9) 회귀.

소유자 지시(2026-09-04): 새 스킬을 만들기 전에 **전체 기존 스킬 목록**과 관련 후보의
`SKILL.md` 를 먼저 확인하고, 이름·설명·트리거·스크립트·테스트·게이트를 대조해 재사용
가능성을 먼저 판단해야 한다. 여기서 고정하는 것은 그 판단의 기계 부분이다 —
목록 열거(enumerated)·실제로 읽은 SKILL.md 경로(viewed)·판정(verdict)이 증적으로 남고,
겹치면 초안을 만들지 않는다.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.skill_generation import cli
from automation.skill_generation.core import ProposalStatus, RepetitionDetector
from automation.skill_generation.precheck import VerdictKind, build_catalog, compare
from automation.skill_generation.service import AutoSkillService, SkillGenerationPaths

_RECALL_DESCRIPTION = "개인 RAG 검색 스킬. 기억 질문에 출처 인용"
_REUSE_TEXT = "개인 RAG 검색 스킬로 기억 질문에 출처 인용"
_NOVEL_TEXT = "화분 물주기 요일 알림표를 만들어줘"


def _write_skill(root: Path, name: str, description: str, tags: str = "[Recall, RAG]") -> Path:
    directory = root / name
    _ = (directory / "scripts").mkdir(parents=True)
    _ = (directory / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n  hermes:\n    tags: {tags}\n---\n\n'
        f"# {name}\n\n`!{name} <질문>` 으로 부르거나 “{name} 해줘”라고 말한다.\n"
        "변경 작업은 소유자 승인 ✅ 뒤에만 실행한다.\n",
        encoding="utf-8",
    )
    _ = (directory / "scripts" / f"{name}_cli.py").write_text("", encoding="utf-8")
    return directory


def test_build_catalog_when_one_skill_md_is_unreadable_then_only_that_one_is_skipped(tmp_path: Path) -> None:
    # Given: 정상 스킬 하나와 frontmatter 가 깨진 스킬 하나가 같은 루트에 있다.
    root = tmp_path / "skills"
    _ = _write_skill(root, "recall", _RECALL_DESCRIPTION)
    broken = root / "broken"
    _ = broken.mkdir(parents=True)
    _ = (broken / "SKILL.md").write_text("설명도 태그도 없는 본문\n", encoding="utf-8")

    # When: 카탈로그를 만든다.
    catalog = build_catalog((root,))

    # Then: 깨진 쪽만 사유와 함께 빠지고, 나머지는 트리거·스크립트·게이트까지 채워진다.
    assert tuple(card.name for card in catalog.cards) == ("recall",)
    assert any("broken" in reason for reason in catalog.skipped)
    card = catalog.cards[0]
    assert card.skill_md == root / "recall" / "SKILL.md"
    assert "!recall" in card.triggers
    assert card.scripts == ("recall_cli.py",)
    assert "승인" in card.gates


def test_build_catalog_when_a_root_is_missing_then_the_remaining_roots_are_enumerated(tmp_path: Path) -> None:
    # Given: governed live 마운트가 없는 워크스테이션처럼 루트 하나가 존재하지 않는다.
    present = tmp_path / "present"
    _ = _write_skill(present, "recall", _RECALL_DESCRIPTION)
    _ = _write_skill(present, "todo", "할 일 목록을 관리한다")

    # When: 없는 루트를 섞어 카탈로그를 만든다.
    catalog = build_catalog((tmp_path / "absent", present))

    # Then: 예외 없이 남은 루트만 열거한다.
    assert tuple(card.name for card in catalog.cards) == ("recall", "todo")


def test_build_catalog_when_two_roots_share_a_name_then_the_first_root_wins(tmp_path: Path) -> None:
    # Given: repo 스킬 루트와 governed live 루트가 같은 이름을 들고 있다.
    first = tmp_path / "first"
    second = tmp_path / "second"
    _ = _write_skill(first, "recall", _RECALL_DESCRIPTION)
    _ = _write_skill(second, "recall", "낡은 사본")

    # When: 두 루트를 순서대로 넣는다.
    catalog = build_catalog((first, second))

    # Then: 이름은 한 번만 열거되고 앞 루트의 SKILL.md 를 읽는다.
    assert tuple(card.name for card in catalog.cards) == ("recall",)
    assert catalog.cards[0].skill_md == first / "recall" / "SKILL.md"


def test_compare_when_candidate_repeats_an_existing_description_then_verdict_is_reuse_existing(tmp_path: Path) -> None:
    # Given: recall 과 같은 일을 말로 풀어쓴 후보.
    root = tmp_path / "skills"
    _ = _write_skill(root, "recall", _RECALL_DESCRIPTION)
    _ = _write_skill(root, "todo", "할 일 목록을 관리한다")
    catalog = build_catalog((root,))

    # When: 후보 문장을 대조한다.
    verdict = compare("개인 RAG 검색 스킬로 기억 질문에 출처 인용", "auto-0123456789abcdef", catalog.cards)

    # Then: 신규 제작이 아니라 기존 스킬 재사용으로 판정하고, 근거 낱말과 열거·열람 증적을 남긴다.
    assert verdict.kind is VerdictKind.REUSE_EXISTING
    assert verdict.matches[0].name == "recall"
    assert verdict.matches[0].score >= 0.5
    assert len(verdict.matches[0].shared) >= 5
    assert verdict.enumerated == ("recall", "todo")
    assert str(root / "recall" / "SKILL.md") in verdict.viewed


def test_compare_when_candidate_name_matches_an_existing_skill_then_verdict_is_reuse_existing(tmp_path: Path) -> None:
    # Given: 설명은 전혀 겹치지 않지만 이름이 대소문자만 다른 후보.
    root = tmp_path / "skills"
    _ = _write_skill(root, "recall", _RECALL_DESCRIPTION)
    catalog = build_catalog((root,))

    # When: 이름 충돌만 있는 후보를 대조한다.
    verdict = compare("화분 물주기 요일 알림", "ReCall", catalog.cards)

    # Then: 이름 선점은 그 자체로 재사용 판정이다.
    assert verdict.kind is VerdictKind.REUSE_EXISTING
    assert verdict.matches[0].name == "recall"


def test_compare_when_nothing_overlaps_then_verdict_is_new_with_full_evidence(tmp_path: Path) -> None:
    # Given: 기존 스킬 두 개와 무관한 후보.
    root = tmp_path / "skills"
    _ = _write_skill(root, "recall", _RECALL_DESCRIPTION)
    _ = _write_skill(root, "todo", "할 일 목록을 관리한다")
    catalog = build_catalog((root,))

    # When: 후보를 대조한다.
    verdict = compare("화분 물주기 요일 알림", "auto-0123456789abcdef", catalog.cards)

    # Then: 신규 제작이지만 열거·열람 증적은 똑같이 남는다.
    assert verdict.kind is VerdictKind.NEW
    assert verdict.enumerated == ("recall", "todo")
    assert len(verdict.viewed) == 2


def test_compare_when_run_twice_then_the_verdict_is_identical(tmp_path: Path) -> None:
    # Given: 여러 후보가 걸리는 카탈로그.
    root = tmp_path / "skills"
    _ = _write_skill(root, "recall", _RECALL_DESCRIPTION)
    _ = _write_skill(root, "memo", _RECALL_DESCRIPTION)
    catalog = build_catalog((root,))

    # When: 같은 입력으로 두 번 대조한다.
    first = compare("개인 RAG 검색 스킬로 기억 질문에 출처 인용", "auto-0123456789abcdef", catalog.cards)
    second = compare("개인 RAG 검색 스킬로 기억 질문에 출처 인용", "auto-0123456789abcdef", catalog.cards)

    # Then: LLM 없는 결정적 판정이라 순서까지 같다.
    assert first == second
    assert tuple(match.name for match in first.matches) == ("memo", "recall")


def _service(tmp_path: Path, roots: tuple[Path, ...]) -> AutoSkillService:
    paths = replace(SkillGenerationPaths.from_root(tmp_path / "state"), catalog_roots=roots)
    return AutoSkillService(paths, RepetitionDetector(), None)


def _reviews(tmp_path: Path) -> tuple[dict[str, object], ...]:
    path = tmp_path / "state" / "reviews.jsonl"
    if not path.is_file():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)


def test_observe_when_an_existing_skill_covers_the_pattern_then_no_draft_and_the_review_row_proves_it(tmp_path: Path) -> None:
    # Given: recall 과 기능이 겹치는 요청이 같은 ISO 주에 세 번 반복된다.
    root = tmp_path / "skills"
    _ = _write_skill(root, "recall", _RECALL_DESCRIPTION)
    service = _service(tmp_path, (root,))
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)
    for day in (2, 1):
        _ = service.observe(_REUSE_TEXT, now - timedelta(days=day))

    # When: 임계값에 도달해 제작 판단이 일어난다.
    proposal = service.observe(_REUSE_TEXT, now)

    # Then: 초안을 만들지 않고 재사용으로 기록하며, 열거·열람 증적이 reviews.jsonl 에 남는다.
    assert proposal is not None
    assert proposal.status is ProposalStatus.REUSE_EXISTING
    assert not proposal.draft_dir.exists()
    rows = _reviews(tmp_path)
    assert len(rows) == 1
    assert rows[0]["verdict"] == "REUSE-EXISTING"
    assert rows[0]["name"] == proposal.name
    assert rows[0]["week"] == proposal.week
    assert rows[0]["enumerated"] == ["recall"]
    assert rows[0]["viewed"] == [str(root / "recall" / "SKILL.md")]
    assert rows[0]["matches"][0]["name"] == "recall"  # pyright: ignore[reportIndexIssue]

    # Then: 같은 패턴이 또 와도 판정 한 건에 한 줄 — 관측마다 쌓지 않는다.
    assert service.observe(_REUSE_TEXT, now) is None
    assert len(_reviews(tmp_path)) == 1


def test_observe_when_the_pattern_matches_nothing_then_the_draft_carries_the_comparison(tmp_path: Path) -> None:
    # Given: 기존 스킬과 겹치지 않는 요청이 세 번 반복된다.
    root = tmp_path / "skills"
    _ = _write_skill(root, "recall", _RECALL_DESCRIPTION)
    service = _service(tmp_path, (root,))
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)
    for day in (2, 1):
        _ = service.observe(_NOVEL_TEXT, now - timedelta(days=day))

    # When: 임계값에 도달한다.
    proposal = service.observe(_NOVEL_TEXT, now)

    # Then: 초안은 그대로 만들되 기존 스킬 대조 결과를 SKILL.md 에 싣는다.
    assert proposal is not None
    assert proposal.status is ProposalStatus.SUGGESTED
    body = (proposal.draft_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "## 기존 스킬 대조" in body
    assert "조회한 기존 스킬 1개" in body
    assert "차이:" in body
    assert "재사용 구성요소: 없음" in body
    rows = _reviews(tmp_path)
    assert len(rows) == 1
    assert rows[0]["verdict"] == "NEW"


def test_latest_review_when_no_decision_was_made_for_that_name_then_it_is_absent(tmp_path: Path) -> None:
    # Given: 아직 어떤 제작 판단도 없었다.
    service = _service(tmp_path, (tmp_path / "skills",))

    # When: 없는 이름의 증적을 묻는다.
    review = service.latest_review("auto-0123456789abcdef")

    # Then: 없다고 답한다 — 지어내지 않는다.
    assert review is None


def test_cli_when_the_repeated_request_reuses_a_mounted_skill_then_it_prints_the_verdict_and_keeps_the_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: governed live 마운트에 같은 일을 하는 스킬이 이미 있고, 같은 요청이 세 번 온다.
    live = tmp_path / "live"
    _ = _write_skill(live, "greenhouse-water", "화분 물주기 요일 알림표 관리", tags="[Greenhouse]")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(live))
    text = "화분 물주기 요일 알림표 관리 자동화"
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)
    detector = RepetitionDetector()
    name = detector.draft_name(detector.observation(text, now))

    # When: 게이트웨이 대신 CLI 로 같은 관측을 흘린다.
    for day in (2, 1, 0):
        monkeypatch.setattr(
            sys, "argv", ["cli.py", "observe", "--text", text, "--timestamp", (now - timedelta(days=day)).isoformat()]
        )
        assert cli.main() == 0
    printed = capsys.readouterr().out

    # Then: 운영자가 어떤 기존 스킬을 패치해야 하는지 판정과 이름으로 안다.
    assert f"PRECHECK REUSE-EXISTING {name} matches=greenhouse-water" in printed
    assert "SUGGESTION" not in printed

    # Then: 같은 증적을 `review` 로 다시 꺼낼 수 있고, 없는 이름은 exit 1 이다.
    monkeypatch.setattr(sys, "argv", ["cli.py", "review", name])
    assert cli.main() == 0
    row = json.loads(capsys.readouterr().out)
    assert row["verdict"] == "REUSE-EXISTING"
    assert "greenhouse-water" in row["enumerated"]
    assert row["viewed"]
    monkeypatch.setattr(sys, "argv", ["cli.py", "review", "auto-ffffffffffffffff"])
    assert cli.main() == 1
