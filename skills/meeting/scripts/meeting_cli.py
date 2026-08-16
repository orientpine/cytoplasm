"""Meeting ingest CLI (W2-3): file/body -> gate -> LLM -> Kanban/milestones/#team.

Deterministic pipeline wrapper. All content stays out of argv/logs; the
routing log records metadata only (filenames, counts, provider), never text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import meeting_actions
import meeting_extract
import meeting_gate
import meeting_llm

KST = ZoneInfo("Asia/Seoul")


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def _config() -> dict:
    config_path = _env_path("MEETING_CONFIG", "~/.hermes/meeting/config.json")
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    return {}


def _log(record: dict) -> None:
    log_dir = _env_path("MEETING_LOG_DIR", "~/.hermes/meeting/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"ingest-{datetime.now(KST).strftime('%Y%m%d')}.jsonl"
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    log_file.chmod(0o600)


def _transport(channel_id: str):
    runtime = _env_path("INTEROP_RUNTIME", "~/.hermes/interop_runtime")
    sys.path.insert(0, str(runtime))
    from automation.interop.discord_transport import DiscordTransport  # noqa: PLC0415

    return DiscordTransport(token=os.environ["DISCORD_BOT_TOKEN"], channel_id=channel_id)


def _notify(channel_id: str | None, message: str, *, offline_dir: Path | None) -> None:
    if not channel_id:
        return
    if offline_dir is not None:
        (offline_dir / "notify.txt").write_text(
            f"{channel_id}\n{message}\n", encoding="utf-8"
        )
        return
    _transport(channel_id).send(message)


def _run_kanban(card: meeting_actions.PlannedCard) -> str:
    completed = subprocess.run(
        ["hermes", *card.argv()],
        capture_output=True,
        timeout=120,
        cwd=os.path.expanduser("~"),
        check=True,
    )
    try:
        return json.loads(completed.stdout.decode("utf-8", errors="replace"))["id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return "unknown"


def cmd_ingest(args: argparse.Namespace) -> int:
    started = time.monotonic()
    config = _config()
    now = datetime.now(KST)
    offline_dir = _env_path("MEETING_PLAN_DIR", "~/.hermes/meeting/plan") if args.offline else None
    if offline_dir is not None:
        offline_dir.mkdir(parents=True, exist_ok=True)
    label = args.label or (Path(args.file).name if args.file else "!meeting 본문")
    record: dict = {"ts": now.isoformat(timespec="seconds"), "label": label, "glm_called": False}

    try:
        if args.file:
            extracted = meeting_extract.extract_file(Path(args.file))
        else:
            extracted = meeting_extract.extract_body(
                Path(args.body_file).read_text(encoding="utf-8", errors="replace")
            )
    except meeting_extract.ExtractionRefused as refusal:
        print(refusal.notice)
        _notify(args.notify_channel, refusal.notice, offline_dir=offline_dir)
        record.update({"exit": refusal.exit_code, "refused": True})
        _log(record)
        return refusal.exit_code

    rules = meeting_gate.load_rules(
        _env_path(
            "MEETING_RULES_FILE",
            "/srv/autophagy-skills/live/meeting/configs/sensitivity-rules.yaml",
        )
    )
    gate = meeting_gate.evaluate(extracted.text, rules)
    ref = hashlib.sha256(extracted.text.encode("utf-8")).hexdigest()[:8]
    record.update(
        {"kind": extracted.kind, "bytes": extracted.input_bytes, "ref": ref,
         "sensitive": gate.sensitive, "tags": list(gate.tags)}
    )

    recorded = (
        Path(args.recorded_response).read_text(encoding="utf-8")
        if args.recorded_response
        else None
    )
    try:
        extraction, provider = meeting_llm.extract(
            extracted.text,
            sensitive=gate.sensitive,
            prompt_path=_env_path(
                "MEETING_PROMPT_FILE",
                "/srv/autophagy-skills/live/meeting/prompts/meeting-extraction-v3.md",
            ),
            my_names=str(config.get("my_names", "cha,차")),
            base_url=os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:4000/v1"),
            api_key=os.environ.get("LITELLM_AGENT_KEY", ""),
            recorded_response=recorded,
        )
    except (meeting_llm.ExtractionParseError, meeting_llm.PatentRoutingError, OSError) as error:
        notice = "회의록 추출 실패: LLM 응답을 해석하지 못했습니다. 다시 시도해 주세요."
        print(notice)
        _notify(args.notify_channel, notice, offline_dir=offline_dir)
        record.update({"exit": 6, "error": type(error).__name__})
        _log(record)
        return 6
    record["provider"] = provider
    record["glm_called"] = provider == meeting_llm.GLM_MODEL

    sensitive_label = "민감 회의" if gate.sensitive else label
    note_path = meeting_actions.write_note(
        _env_path("MEETING_NOTES_DIR", "~/notes/meetings"),
        label=sensitive_label,
        kind=extracted.kind,
        original_text=extracted.text,
        extraction=extraction,
        sensitive=gate.sensitive,
        ref=ref,
        now=now,
    )

    cards = meeting_actions.plan_cards(
        extraction, sensitive=gate.sensitive, note_name=note_path.name, ref=ref,
        rules=rules,
    )
    card_ids: list[str] = []
    if offline_dir is not None:
        (offline_dir / "kanban-plan.jsonl").write_text(
            "".join(json.dumps(card.argv(), ensure_ascii=False) + "\n" for card in cards),
            encoding="utf-8",
        )
    else:
        card_ids = [_run_kanban(card) for card in cards]

    milestones_added = meeting_actions.update_milestones(
        _env_path("MEETING_STATE_FILE", "~/state/milestones.yaml"),
        extraction.milestones,
        sensitive=gate.sensitive,
        note_name=note_path.name,
        ref=ref,
        now=now,
    )

    team_posted = False
    team_post = meeting_actions.format_team_post(
        extraction.others, agent_id=str(config.get("agent_id", "agent")), ref=ref, now=now
    )
    if team_post and not gate.sensitive:
        if offline_dir is not None:
            (offline_dir / "team-post.txt").write_text(team_post, encoding="utf-8")
        else:
            team_channel = str(config.get("team_channel_id", ""))
            if team_channel:
                _transport(team_channel).send(team_post)
                team_posted = True

    notice = meeting_actions.format_notify(
        label=label,
        sensitive=gate.sensitive,
        cards=len(cards),
        milestones_added=milestones_added,
        others=len(extraction.others),
        note_name=note_path.name,
        team_posted=team_posted,
    )
    _notify(args.notify_channel, notice, offline_dir=offline_dir)

    record.update(
        {"exit": 0, "todos": len(extraction.todos), "milestones": len(extraction.milestones),
         "others": len(extraction.others), "cards": len(cards), "card_ids": card_ids,
         "milestones_added": milestones_added, "note": note_path.name,
         "team_posted": team_posted, "elapsed_s": round(time.monotonic() - started, 1)}
    )
    _log(record)
    output = {key: record[key] for key in
              ("exit", "ref", "sensitive", "provider", "glm_called", "todos",
               "milestones", "others", "cards", "milestones_added", "note", "team_posted")}
    print(json.dumps(output, ensure_ascii=False))
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    rules = meeting_gate.load_rules(
        _env_path(
            "MEETING_RULES_FILE",
            "/srv/autophagy-skills/live/meeting/configs/sensitivity-rules.yaml",
        )
    )
    extracted = meeting_extract.extract_file(Path(args.file))
    gate = meeting_gate.evaluate(extracted.text, rules)
    print(json.dumps({"sensitive": gate.sensitive, "tags": list(gate.tags)}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meeting", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="회의록 파일/본문 인제스트")
    source = ingest.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="md/txt/pdf 파일 경로")
    source.add_argument("--body-file", help="!meeting 본문이 담긴 임시 파일")
    ingest.add_argument("--label", help="회의 라벨(기본: 파일명)")
    ingest.add_argument("--notify-channel", help="결과 통지 Discord 채널 ID")
    ingest.add_argument("--recorded-response", help="녹화된 LLM 응답 파일(테스트 전용)")
    ingest.add_argument("--offline", action="store_true", help="외부 부작용 없이 계획만 기록")
    ingest.set_defaults(func=cmd_ingest)

    gate = subparsers.add_parser("gate", help="민감도 게이트 단독 평가")
    gate.add_argument("--file", required=True)
    gate.set_defaults(func=cmd_gate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
