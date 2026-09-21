"""Real CLI -> wrapper -> isolated SQLite/Markdown, without external services."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

SCRIPTS: Final = Path(__file__).resolve().parents[2] / "skills/mail/scripts"
DOCUMENT: Final = ('---\nsubject: "특허 검토"\nfrom: "sender@example.invalid"\n'
                   'to: "recipient@example.invalid"\ncc: "copy@example.invalid"\n'
                   '---\n\n# 특허 검토\n\n## Body\n\n새 본문\n\n\n\n'
                   '-----원본 메시지-----\n옛 본문\n')


@pytest.fixture()
def mail_env(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "mail.md").write_text(DOCUMENT, encoding="utf-8")
    with sqlite3.connect(tmp_path / "state.db") as connection:
        connection.execute(
            "CREATE TABLE messages (uid TEXT, folder TEXT, subject TEXT, sender TEXT, "
            "recv_date TEXT, markdown_path TEXT, saved_at INTEGER)"
        )
        connection.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (
            "fixture", "inbox", "특허 검토", "sender@example.invalid", "2026-09-01", "mail.md", 1,
        ))
    return {**os.environ, "HOME": str(tmp_path), "MAIL_WRAPPER_REPO": str(tmp_path),
            "MAIL_WRAPPER_DB": str(tmp_path / "state.db")}


def run_cli(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([sys.executable, str(SCRIPTS / "triage_cli.py"), *argv],
                          env=env, capture_output=True, timeout=15, check=False)


def test_default_when_owner_requests_body(mail_env: dict[str, str]) -> None:
    # Given: the existing wrapper's body contains vendor headers and quoted history.
    # When
    result = run_cli(["get", "fixture", "--body"], mail_env)
    # Then
    assert result.returncode == 0, result.stderr.decode()
    text = result.stdout.decode()
    assert text.startswith("- 제목: 특허 검토\n")
    assert "새 본문" in text and "옛 본문" not in text and "\n\n\n" not in text


def test_raw_round_trip_when_requested(mail_env: dict[str, str]) -> None:
    # Given
    # When
    result = run_cli(["get", "fixture", "--body", "--raw"], mail_env)
    # Then: no extra newline, stripping, or JSON quoting.
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == DOCUMENT.encode("utf-8")


def test_full_when_original_is_requested(mail_env: dict[str, str]) -> None:
    # Given
    # When
    result = run_cli(["get", "fixture", "--body", "--full"], mail_env)
    # Then
    assert result.returncode == 0, result.stderr.decode()
    assert "옛 본문" in result.stdout.decode() and "--full" not in result.stdout.decode()


@pytest.mark.parametrize("flags", [[], ["--raw"], ["--full"]])
def test_masking_when_any_view_is_requested(mail_env: dict[str, str], flags: list[str]) -> None:
    # Given: the wrapper remains the authority for masking, including hashes and byte counts.
    argv = ["get", "fixture", "--body", "--masked"]
    expected = subprocess.run([sys.executable, str(SCRIPTS / "mail_wrapper.py"), *argv],
                              env=mail_env, capture_output=True, timeout=15, check=False)
    # When
    result = run_cli([*argv, *flags], mail_env)
    # Then
    assert result.returncode == expected.returncode == 0, result.stderr.decode()
    assert result.stdout == expected.stdout
    payload = json.loads(result.stdout)
    assert "body" not in payload["mail"] and "body_sha256" in payload["mail"]


def test_metadata_when_body_is_not_requested(mail_env: dict[str, str]) -> None:
    # Given
    argv = ["get", "fixture"]
    expected = subprocess.run([sys.executable, str(SCRIPTS / "mail_wrapper.py"), *argv],
                              env=mail_env, capture_output=True, timeout=15, check=False)
    # When
    result = run_cli(argv, mail_env)
    # Then
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == expected.stdout


def test_error_when_uid_is_missing(mail_env: dict[str, str]) -> None:
    # Given
    argv = ["get", "missing", "--body"]
    expected = subprocess.run([sys.executable, str(SCRIPTS / "mail_wrapper.py"), *argv],
                              env=mail_env, capture_output=True, timeout=15, check=False)
    # When
    result = run_cli(argv, mail_env)
    # Then
    assert result.returncode == expected.returncode == 5
    assert result.stdout == expected.stdout


def test_chunks_when_cli_body_is_long(mail_env: dict[str, str]) -> None:
    # Given
    paragraphs = [str(i) + "나" * 950 for i in range(4)]
    Path(mail_env["MAIL_WRAPPER_REPO"], "mail.md").write_text("\n\n".join(paragraphs))
    # When
    result = run_cli(["get", "fixture", "--body"], mail_env)
    # Then: suffixes delimit pasteable chunks on stdout.
    assert result.returncode == 0, result.stderr.decode()
    text = result.stdout.decode().rstrip("\n")
    chunks = text.split("\n\n(")
    assert len(chunks) == 5
    for paragraph in paragraphs:
        assert paragraph in text
