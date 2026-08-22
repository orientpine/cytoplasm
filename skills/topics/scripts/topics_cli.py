from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = _SCRIPTS_DIR.parents[2]
if (REPO_ROOT / "skills" / "topics" / "scripts").is_dir():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
else:
    # Deployed layout: /srv/autophagy-skills/releases/topics/<sha256>/scripts/ —
    # no importable `skills` package sits above it, so synthesize `skills` and
    # `skills.topics` with __path__ at the skill root, exactly like doctype and
    # procurement do. The naive parents[3] insert died in production with
    # ModuleNotFoundError on 2026-08-22 (masked in the sandbox by a
    # namespace-package accident of the ~/.hermes/skills staging path).
    import types

    _SKILL_ROOT = _SCRIPTS_DIR.parent
    if "skills" not in sys.modules:
        _pkg = types.ModuleType("skills")
        _pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["skills"] = _pkg
    if "skills.topics" not in sys.modules:
        _sk = types.ModuleType("skills.topics")
        _sk.__path__ = [str(_SKILL_ROOT)]  # type: ignore[attr-defined]
        sys.modules["skills.topics"] = _sk
        setattr(sys.modules["skills"], "topics", _sk)

from skills.topics.scripts import topics_evidence, topics_knowledge, topics_registry  # noqa: E402


def _state_path() -> Path:
    return Path(os.environ.get("TOPICS_STATE_FILE", str(topics_registry.DEFAULT_STATE_PATH))).expanduser()


def _rules_path() -> Path:
    override = os.environ.get("TOPICS_RULES_PATH")
    return Path(override).expanduser() if override else topics_registry.default_rules_path()


def _topic(words: list[str]) -> str:
    return " ".join(words)


def main(argv: list[str] | None = None, evidence_pack: object | None = None) -> int:
    parser = argparse.ArgumentParser(prog="topics_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("add", "remove", "suggest"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("topic", nargs="+")
    listing = commands.add_parser("list")
    listing.add_argument("--with-evidence", action="store_true")
    evidence = commands.add_parser("evidence")
    evidence.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    state = _state_path()
    try:
        if args.command in {"list", "evidence"}:
            topics = topics_registry.list_topics(state)
            pack = evidence_pack
            if (args.command == "evidence" or args.with_evidence) and pack is None:
                pack = topics_knowledge.collect(topics)
            if args.command == "evidence":
                assert pack is not None
                print(topics_evidence.preview(pack, as_json=bool(args.json)))
                topics_evidence.write_sidecar(state, pack)
                return 0
            if topics:
                print("TOPICS-LIST")
                for topic in topics:
                    print(f"- {topic}")
            else:
                print("TOPICS-EMPTY 등록된 주제가 없습니다.")
            if pack is not None:
                print(topics_evidence.render_section(pack))
                topics_evidence.write_sidecar(state, pack)
            return 0
        topic = _topic(args.topic)
        if args.command == "remove":
            removed = topics_registry.remove_topic(state, topic)
            print("TOPIC-REMOVED" if removed else "TOPIC-ABSENT")
            return 0
        decision = topics_registry.validate_suggestion(topic, topics_registry.load_rules(_rules_path()))
        if not decision.accepted:
            prefix = "TOPIC-REFUSED" if args.command == "add" else "TOPIC-SUGGEST-REFUSED"
            print(f"{prefix} {decision.guidance}")
            return 0
        if args.command == "suggest":
            print(f"TOPIC-SUGGEST {decision.topic}")
            return 0
        added = topics_registry.add_topic(state, decision.topic, topics_registry.load_rules(_rules_path()))
        print(f"{'TOPIC-EXISTS' if added.duplicate else 'TOPIC-ADDED'} {added.topic}")
        return 0
    except topics_registry.RegistryError as error:
        print(f"TOPICS-ERROR {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
