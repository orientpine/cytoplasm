from __future__ import annotations

from automation.managed_skills.publish_command import main
from automation.managed_skills.publish_core import (
    PublishConfig as PublishConfig,
    PublishError as PublishError,
    Runner as Runner,
    SelfDigestReclaimError as SelfDigestReclaimError,
)
from automation.managed_skills.publish_release import publish as publish
from automation.skill_review import skill_digest as skill_digest


if __name__ == "__main__":
    raise SystemExit(main())
