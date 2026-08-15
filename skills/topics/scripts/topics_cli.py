from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from skills.topics.scripts import topics_registry


def _state_path() -> Path:
    return Path(os.environ.get("TOPICS_STATE_FILE", str(topics_registry.DEFAULT_STATE_PATH))).expanduser()


def _rules_path() -> Path:
    override = os.environ.get("TOPICS_RULES_PATH")
    return Path(override).expanduser() if override else topics_registry.default_rules_path()


def _topic(words: list[str]) -> str:
    return " ".join(words)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="topics_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("add", "remove", "suggest"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("topic", nargs="+")
    commands.add_parser("list")
    args = parser.parse_args(argv)
    state = _state_path()
    try:
        if args.command == "list":
            topics = topics_registry.list_topics(state)
            if topics:
                print("TOPICS-LIST")
                for topic in topics:
                    print(f"- {topic}")
            else:
                print("TOPICS-EMPTY 등록된 주제가 없습니다.")
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
