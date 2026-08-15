from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

# Deterministic sentinel the recall skill prefixes to every patent-sensitive
# document it releases into an agent conversation (only when the agent's
# primary model route is non-GLM — skills/recall/scripts/recall_cli.py).
# If such a conversation later reaches glm-main anyway (rate-limit/outage
# failover to the fallback chain), this pre-call guard refuses the request so
# patent content never reaches the GLM provider. Must stay byte-identical to
# SENSITIVE_MARKER in recall_cli.py (cross-checked by unit test).
PATENT_SENTINEL = "[[PATENT-SENSITIVE-RECALL]]"

_REJECTION_DETAIL = (
    "no_deployments_with_tag_routing: glm-main has no patent-sensitive "
    "deployment; route to a non-GLM provider."
)


def payload_contains_sentinel(data) -> bool:
    """True when any message content in the request carries the sentinel."""
    for message in data.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and PATENT_SENTINEL in content:
            return True
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and PATENT_SENTINEL in str(
                    part.get("text") or ""
                ):
                    return True
    return False


class PatentSensitiveGlmBlocker(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if data.get("model") != "glm-main":
            return data
        metadata = data.get("metadata") or {}
        tags = set(data.get("tags") or []) | set(metadata.get("tags") or [])
        if "patent-sensitive" in tags or payload_contains_sentinel(data):
            raise HTTPException(status_code=403, detail=_REJECTION_DETAIL)
        return data


proxy_handler_instance = PatentSensitiveGlmBlocker()