"""Cached, source-grounded mailon interface contract for the read-only wrapper."""

from __future__ import annotations

WRAPPER_VERSION = "mail-wrapper-v1"

# Cached from docs/guide/기관메일-인터페이스.md (W0-7a/b/c).
MAILON_INTERFACE = {
    "module": "mailon.main",
    "commands": ("totp", "login", "probe", "sync", "status", "send", "resolve"),
    "read_only_commands": ("status", "sync", "resolve"),
    "json_capable_commands": ("send", "resolve"),
    "exit_codes": {
        0: "ok",
        1: "config_error",
        2: "auth_or_browser_error",
        3: "sync_crash",
        10: "win_launcher_no_venv",
        11: "win_launcher_no_agent_browser",
    },
    "collection": {"page_size": 20, "max_pages": 500},
    "stdout_patterns": {
        "sync_ok": r"^OK: (?P<new>\d+) new mail\(s\)",
        "login_ok": r"^OK: (?P<url>\S+)",
        "status_saved": r"^Saved mails: (?P<count>\d+)$",
        "status_no_db": r"^No database yet\.",
        "status_last_run": (
            r"^Last run #(?P<run_id>\d+): status=(?P<status>\S+) "
            r"new=(?P<new>\d+) started=(?P<started>\S+)"
        ),
    },
    "send_json_keys": (
        "attachment_count", "attachment_manifest_sha256", "csrf_present",
        "network_post_count", "status", "verified",
    ),
    "resolve_json_keys": ("candidates", "post_count", "query", "status"),
    "send_error_json_keys": (
        "error_code", "message", "retryable", "stage", "status",
    ),
    "send_error_codes": (
        "attachment_invalid",
        "attachment_unsupported",
        "attachment_upload_failed",
        "auth_error",
        "confirmation_required",
        "external_service_error",
        "send_failed",
        "send_unverified",
        "validation_error",
    ),    "failure_signatures": {
        "inbox_folder_uid_selector": "folderUid",
        "login_dom_ipt_id": "ipt-id",
        "timeout": "Timeout",
        "login_error": "LoginError",
        "browser_error": "BrowserError",
    },
}

WRAPPER_EXIT = {
    "ok": 0,
    "config_error": 1,
    "auth_error": 2,
    "read_path_error": 3,
    "usage_error": 4,
    "not_found": 5,
    "environment_error": 6,
    "timeout": 7,
}

REAUTH_GUIDANCE = (
    "기관메일 인증 실패(mailon exit 2 = auth_or_browser_error). 재인증 절차: "
    "① ~agent/.env.secrets의 MAILON_ID/MAILON_PW/MAILON_TOTP_SECRET 존재·길이 확인 "
    "② 잔여 브라우저 세션 정리(pkill -u agent -f chrome) 후 재시도 — 중단된 sync 뒤 "
    "'ipt-id' 로그인 실패는 이 잔여 세션이 원인 "
    "③ 수동 검증: cd ~/emailAutomation && set -a; . ~/.env.secrets; set +a; "
    ".venv/bin/python -m mailon.main login "
    "④ 반복 실패 시 자동 재시도 금지 — cha 에스컬레이션 및 mail-mode 재판정(W4-2 규칙)."
)
CONFIG_GUIDANCE = (
    "mailon 설정 오류(exit 1): ~agent/.env.secrets에 MAILON_ID/MAILON_PW/"
    "MAILON_TOTP_SECRET가 있는지, 래퍼가 그 파일을 읽을 수 있는지 확인."
)
SYNC_FALLBACK_NOTE = (
    "mailon sync 구조 실패(exit 3) — 로컬 state.db 읽기로 폴백함. "
    "W0-7a의 folderUid selector 실패가 재발한 경우 정확한 시그니처를 "
    "docs/qa에 기록하고 selector 수정 전 재시도하지 말 것."
)

MAILON_ENV_ALLOWLIST = (
    "MAILON_ID", "MAILON_PW", "MAILON_TOTP_SECRET",
    "MAILON_LOGIN_URL", "HEADLESS", "MAX_MAILS_PER_RUN",
)
SYSTEM_ENV_KEEP = (
    "HOME", "PATH", "LANG", "LC_ALL", "TZ", "TERM", "USER", "LOGNAME",
    "SHELL", "TMPDIR", "XDG_RUNTIME_DIR",
)
