"""``automation.plaud_sync.rename_legacy`` — one-shot rename of pre-2026-09-15 lifelog notes.

The naming change (note_paths) only governs notes written from now on; the 13 notes already
in the vault carried ``<날짜>-<슬러그>--<12hex>.md`` names and titles that were sometimes the
stem itself. This CLI plans the new ``YYYY-MM-DD HHMM <제목>`` names from each note's own
frontmatter, renames with ``git mv``, rewrites ``title:``/``# H1`` and moves the node's
``state.json`` ``note_relpath`` so a reprocess upserts the renamed file.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from automation.plaud_sync.model import PlaudStatus, PlaudSyncRecord, PlaudSyncState
from automation.plaud_sync.store import load_state, save_state
from automation.plaud_sync.rename_legacy import (
    Rename,
    Skip,
    apply,
    main,
    migrate_state,
    plan,
    rewrite_note,
)

_LIFELOG = Path("000_PARA/Area/Lifelog/2026")


def _note(title_line: str, created: str, h1: str) -> str:
    return (
        "---\n"
        "tags: [lifelog]\n"
        f"{title_line}\n"
        "source: PLAUD 녹음 abc\n"
        f"created: {created}\n"
        f"modified: {created}\n"
        "---\n"
        "\n"
        f"# {h1}\n"
        "\n"
        "## 한눈에\n"
        "\n"
        "- 녹음:: 2026-09-10 (목) 10:41 · 24분 16초\n"
        "\n"
        "## 요약\n"
        "\n"
        "- 내용\n"
    )


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    year = vault / _LIFELOG
    year.mkdir(parents=True)
    (year / "2026-09-10-104144--3e634d57a4c1.md").write_text(
        _note("title: 출산 당일 병원 이동과 입실 준비 (2026-09-10)", "2026-09-10T10:41:44",
              "출산 당일 병원 이동과 입실 준비 (2026-09-10)"),
        encoding="utf-8",
    )
    (year / "2026-09-08-연구원-예산인사운영-현안-공유--32a5a49e1ff9.md").write_text(
        _note('title: "연구원 예산·인사·운영 현안: 공유 (2026-09-08)"', "2026-09-08T16:02:11",
              "2026-09-08-연구원-예산인사운영-현안-공유--32a5a49e1ff9"),
        encoding="utf-8",
    )
    (year / "2026-09-09-110506-자율팀-과제--cdd47d0c7384.md").write_text(
        _note("title: 2026-09-09-110506-자율팀-과제--cdd47d0c7384", "2026-09-09T11:05:06",
              "2026-09-09-110506-자율팀-과제--cdd47d0c7384"),
        encoding="utf-8",
    )
    (year / "2026-09-04-180427--7df8fc0f016b.md").write_text(
        _note("title: 2026-09-04 18:04:27 (2026-09-04)", "2026-09-04T18:04:27",
              "2026-09-04-180427--7df8fc0f016b"),
        encoding="utf-8",
    )
    (year / "2026-09-02-09-02-직장-동료들의-일상-대화--738de04490ec.md").write_text(
        "# 2026-09-02-09-02-직장-동료들의-일상-대화--738de04490ec\n\n>[!info]\n> Author: cha\n\n## 요약\n\n- 내용\n",
        encoding="utf-8",
    )
    (year / "2026-09-14_1302_이미_새_이름.md").write_text(
        _note("title: 2026-09-14_1302_이미_새_이름", "2026-09-14T13:02:20", "2026-09-14_1302_이미_새_이름"),
        encoding="utf-8",
    )
    return vault


def test_plan_derives_the_new_stem_from_frontmatter_and_reports_what_it_cannot_name(tmp_path: Path) -> None:
    vault = _vault(tmp_path)

    renames, skips = plan(vault, overrides={})

    assert {r.old_name: r.new_name for r in renames} == {
        "2026-09-10-104144--3e634d57a4c1.md": "2026-09-10_1041_출산_당일_병원_이동과_입실_준비.md",
        "2026-09-08-연구원-예산인사운영-현안-공유--32a5a49e1ff9.md": "2026-09-08_1602_연구원_예산·인사·운영_현안_공유.md",
        # title == old stem → the slug is the only title there is: stamps and digest off, hyphens to spaces.
        "2026-09-09-110506-자율팀-과제--cdd47d0c7384.md": "2026-09-09_1105_자율팀_과제.md",
    }
    assert all(r.relpath.parent == _LIFELOG for r in renames)
    assert {s.old_name: s.reason for s in skips} == {
        "2026-09-04-180427--7df8fc0f016b.md": "no title — pass --stem",
        "2026-09-02-09-02-직장-동료들의-일상-대화--738de04490ec.md": "no frontmatter created — pass --stem",
    }
    # Already-new-format notes are neither renamed nor reported.
    assert "2026-09-14_1302_이미_새_이름.md" not in {r.old_name for r in renames} | {s.old_name for s in skips}


def test_plan_treats_the_space_separated_stem_of_2026_09_15_as_legacy(tmp_path: Path) -> None:
    """같은 날 먼저 앉은 공백 양식(`YYYY-MM-DD HHMM 제목`)도 옛 이름이다 — title 이 stem 이라 날짜·시각을
    두 번 적지 않게 앞머리를 걷어낸 뒤 `_` 로 잇는다.
    """
    vault = tmp_path / "vault"
    year = vault / _LIFELOG
    year.mkdir(parents=True)
    (year / "2026-09-10 1041 출산 당일 병원 이동과 입실 준비.md").write_text(
        _note("title: 2026-09-10 1041 출산 당일 병원 이동과 입실 준비", "2026-09-10T10:41:44",
              "2026-09-10 1041 출산 당일 병원 이동과 입실 준비"),
        encoding="utf-8",
    )

    renames, skips = plan(vault, overrides={})

    assert [(r.old_name, r.new_name) for r in renames] == [
        ("2026-09-10 1041 출산 당일 병원 이동과 입실 준비.md", "2026-09-10_1041_출산_당일_병원_이동과_입실_준비.md")
    ]
    assert skips == ()


def test_plan_takes_owner_overrides_and_rejects_malformed_or_colliding_stems(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    overrides = {
        "2026-09-04-180427--7df8fc0f016b.md": "2026-09-04_1804_가족_식사와_에이전트_활용_설명",
        "2026-09-02-09-02-직장-동료들의-일상-대화--738de04490ec.md": "2026-09-02_0902_직장_동료들의_일상_대화",
    }

    renames, skips = plan(vault, overrides=overrides)

    assert {r.old_name: r.new_name for r in renames}["2026-09-04-180427--7df8fc0f016b.md"] == (
        "2026-09-04_1804_가족_식사와_에이전트_활용_설명.md"
    )
    assert skips == ()
    with pytest.raises(ValueError, match="stem"):
        _ = plan(vault, overrides={"2026-09-04-180427--7df8fc0f016b.md": "가족 식사"})
    colliding = plan(
        vault, overrides={"2026-09-04-180427--7df8fc0f016b.md": "2026-09-10_1041_출산_당일_병원_이동과_입실_준비"}
    )
    assert [s.reason for s in colliding[1] if s.old_name == "2026-09-04-180427--7df8fc0f016b.md"] == [
        "collides with 2026-09-10_1041_출산_당일_병원_이동과_입실_준비.md"
    ]


def test_rewrite_note_updates_the_title_and_h1_and_nothing_else() -> None:
    text = _note('title: "연구원 예산·인사·운영 현안: 공유 (2026-09-08)"', "2026-09-08T16:02:11",
                 "2026-09-08-연구원-예산인사운영-현안-공유--32a5a49e1ff9")

    rewritten = rewrite_note(text, "2026-09-08_1602_연구원_예산·인사·운영_현안_공유")

    assert "\ntitle: 2026-09-08_1602_연구원_예산·인사·운영_현안_공유\n" in rewritten
    assert "\n# 2026-09-08_1602_연구원_예산·인사·운영_현안_공유\n" in rewritten
    assert rewritten.count("# ") == text.count("# ")
    assert rewritten.replace("2026-09-08_1602_연구원_예산·인사·운영_현안_공유", "X") == text.replace(
        '"연구원 예산·인사·운영 현안: 공유 (2026-09-08)"', "X"
    ).replace("2026-09-08-연구원-예산인사운영-현안-공유--32a5a49e1ff9", "X")
    # A v1 note without frontmatter gets only its H1 replaced.
    v1 = "# 2026-09-02-09-02-직장--738de04490ec\n\n>[!info]\n> Author: cha\n\n## 요약\n"
    assert rewrite_note(v1, "2026-09-02_0902_직장") == "# 2026-09-02_0902_직장\n\n>[!info]\n> Author: cha\n\n## 요약\n"


def _git_vault(tmp_path: Path) -> Path:
    vault = _vault(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=vault, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=vault, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed"], cwd=vault, check=True
    )
    return vault


def test_apply_git_moves_each_note_rewrites_it_and_writes_the_mapping(tmp_path: Path) -> None:
    vault = _git_vault(tmp_path)
    renames, _skips = plan(vault, overrides={})
    mapping_path = tmp_path / "mapping.json"

    apply(vault, renames, mapping_path=mapping_path)

    year = vault / _LIFELOG
    assert not (year / "2026-09-10-104144--3e634d57a4c1.md").exists()
    moved = (year / "2026-09-10_1041_출산_당일_병원_이동과_입실_준비.md").read_text(encoding="utf-8")
    assert "\ntitle: 2026-09-10_1041_출산_당일_병원_이동과_입실_준비\n" in moved
    assert "\n# 2026-09-10_1041_출산_당일_병원_이동과_입실_준비\n" in moved
    status = subprocess.run(["git", "status", "--porcelain"], cwd=vault, check=True, capture_output=True, text=True).stdout
    assert "R  " in status and "??" not in status, status
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert mapping["000_PARA/Area/Lifelog/2026/2026-09-10-104144--3e634d57a4c1.md"] == (
        "000_PARA/Area/Lifelog/2026/2026-09-10_1041_출산_당일_병원_이동과_입실_준비.md"
    )
    assert len(mapping) == 3


def _record(recording_id: str, relpath: str, status: PlaudStatus) -> PlaudSyncRecord:
    return PlaudSyncRecord(
        version=1,
        recording_id=recording_id,
        recorded_at="2026-09-10T01:41:44+00:00",
        note_relpath=relpath,
        note_title=Path(relpath).stem,
        body_sha256="a" * 64,
        action_hash=f"sha256:{'b' * 64}",
        status=status,
        kind="obsidian-write",
        surface="agent-chat-thread",
        channel_id="",
        policy_version=8,
        message_id=None,
        created_at="2026-09-10T02:00:00+00:00",
        approved_at=None,
        written_at="2026-09-10T02:10:00+00:00" if status == "written" else None,
        remote_ref="refs/remotes/origin/main" if status == "written" else None,
        note_content_sha256=None,
        last_block_reason=None,
    )


def test_migrate_state_moves_written_records_only_and_keeps_their_binding(tmp_path: Path) -> None:
    old = "000_PARA/Area/Lifelog/2026/2026-09-10-104144--3e634d57a4c1.md"
    new = "000_PARA/Area/Lifelog/2026/2026-09-10_1041_출산_당일_병원_이동과_입실_준비.md"
    pending_old = "000_PARA/Area/Lifelog/2026/2026-09-09-110506-자율팀-과제--cdd47d0c7384.md"
    state_path = tmp_path / "state.json"
    save_state(
        state_path,
        PlaudSyncState(1, None, {
            "rec-w": _record("rec-w", old, "written"),
            "rec-p": _record("rec-p", pending_old, "planned"),
            "rec-o": _record("rec-o", "000_PARA/Area/Lifelog/2026/other.md", "written"),
        }),
    )

    moved, skipped = migrate_state(
        state_path, {old: new, pending_old: "000_PARA/Area/Lifelog/2026/2026-09-09_1105_자율팀_과제.md"}
    )

    assert moved == ("rec-w",)
    assert skipped == (("rec-p", "status planned — approval card is bound to the old path"),)
    after = load_state(state_path).records
    assert after["rec-w"].note_relpath == new
    assert after["rec-w"].note_title == "2026-09-10_1041_출산_당일_병원_이동과_입실_준비"
    assert after["rec-w"].action_hash == f"sha256:{'b' * 64}"
    assert after["rec-p"].note_relpath == pending_old
    assert after["rec-o"].note_relpath == "000_PARA/Area/Lifelog/2026/other.md"


def test_main_is_a_dry_run_unless_apply_is_given(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _git_vault(tmp_path)

    rc = main(["--vault", str(vault)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    assert "2026-09-10-104144--3e634d57a4c1.md -> 2026-09-10_1041_출산_당일_병원_이동과_입실_준비.md" in out
    assert "SKIP 2026-09-04-180427--7df8fc0f016b.md: no title — pass --stem" in out
    assert (vault / _LIFELOG / "2026-09-10-104144--3e634d57a4c1.md").exists()


def test_main_apply_with_mapping_and_state_migrates_the_node_record(tmp_path: Path) -> None:
    old = "000_PARA/Area/Lifelog/2026/2026-09-10-104144--3e634d57a4c1.md"
    new = "000_PARA/Area/Lifelog/2026/2026-09-10_1041_출산_당일_병원_이동과_입실_준비.md"
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps({old: new}, ensure_ascii=False), encoding="utf-8")
    state_path = tmp_path / "state.json"
    save_state(state_path, PlaudSyncState(1, None, {"rec-w": _record("rec-w", old, "written")}))

    assert main(["--mapping", str(mapping_path), "--state", str(state_path)]) == 0
    assert load_state(state_path).records["rec-w"].note_relpath == old, "dry-run leaves the state alone"
    assert main(["--mapping", str(mapping_path), "--state", str(state_path), "--apply"]) == 0
    assert load_state(state_path).records["rec-w"].note_relpath == new


def test_plan_and_apply_are_typed_records() -> None:
    assert Rename.__slots__ and Skip.__slots__
