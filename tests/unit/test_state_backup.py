"""SC-3 회귀: 주 1회 암호화 상태 백업 (`automation/state_backup/`).

고정하는 것:

* 아카이브는 allowlist 디렉터리만 싣는다 — 벤더 설치본·interop 설정·심링크·
  ``__pycache__`` 는 실리지 않는다(자격증명 ``~/.env.secrets`` 는 루트가 달라
  구조적으로 포함 불가).
* 평문은 절대 업로드되지 않는다 — 키 부재/노출 권한이면 openssl 호출 전에
  fail-closed 하고, 업로드되는 바이트는 암호화 산출물이다.
* 워터마크는 owner-only + read-back 검증 뒤에만 전진한다 — 같은 ISO 주의 두 번째
  틱은 무음 no-op, 실패한 주는 다음 틱이 재시도한다.
* 세대 보존: 접두사가 일치하는 오래된 세대만 trash 로 정리한다(삭제 없음).
"""
from __future__ import annotations

import json
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.state_backup import backup as sb

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 31, 3, 15, tzinfo=UTC)  # ISO 2026-W36


def _seed_state(home: Path) -> None:
    (home / ".hermes" / "skills" / "demo").mkdir(parents=True)
    _ = (home / ".hermes" / "skills" / "demo" / "SKILL.md").write_text("demo\n")
    (home / ".hermes" / "selfskill-audit").mkdir(parents=True)
    _ = (home / ".hermes" / "selfskill-audit" / "ledger.jsonl").write_text("{}\n")


def _seed_key(home: Path) -> Path:
    key = sb.backup_key_path(home)
    key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = key.write_text("0123456789abcdef\n")
    key.chmod(0o600)
    return key


def _fake_openssl(argv, **_kwargs):
    """Simulate `openssl enc`: write a sealed (non-gzip) blob to -out."""
    dest = Path(argv[argv.index("-out") + 1])
    source = Path(argv[argv.index("-in") + 1])
    _ = dest.write_bytes(b"Salted__FAKE" + source.read_bytes()[:0])
    return subprocess.CompletedProcess(argv, 0, b"", b"")


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.children: list[dict[str, object]] = []

    def ensure_folder_path(self, parts):
        self.calls.append(("ensure", parts))
        return "folder-1"

    def upsert_file(self, local, name, parent_id, prior_id=None):
        self.calls.append(("upsert", name, parent_id, Path(local).read_bytes()[:8]))
        return {"id": "file-1", "sha256": "deadbeef"}

    def verify_owner_only(self, file_id):
        self.calls.append(("owner", file_id))

    def download_and_verify(self, file_id, local):
        self.calls.append(("readback", file_id))
        return "deadbeef"

    def list_children(self, folder_id):
        self.calls.append(("list", folder_id))
        return list(self.children)

    def trash_file(self, file_id):
        self.calls.append(("trash", file_id))
        return str(file_id)


class TestBuildArchive:
    def test_a_allowlist_only(self, tmp_path: Path) -> None:
        _seed_state(tmp_path)
        (tmp_path / ".hermes" / "hermes-agent").mkdir()
        _ = (tmp_path / ".hermes" / "hermes-agent" / "big.bin").write_bytes(b"x" * 64)
        (tmp_path / ".hermes" / "interop").mkdir()
        _ = (tmp_path / ".hermes" / "interop" / "config.json").write_text("{}")
        dest = tmp_path / "out.tar.gz"
        packed = sb.build_archive(tmp_path, sb.DEFAULT_INCLUDE, dest)
        assert packed == 2
        with tarfile.open(dest) as archive:
            names = archive.getnames()
        assert "skills/demo/SKILL.md" in names
        assert "selfskill-audit/ledger.jsonl" in names
        assert not any(n.startswith(("hermes-agent", "interop")) for n in names)

    def test_b_skips_symlink_and_pycache(self, tmp_path: Path) -> None:
        _seed_state(tmp_path)
        secrets = tmp_path / ".env.secrets"
        _ = secrets.write_text("DISCORD_BOT_TOKEN=nope\n")
        (tmp_path / ".hermes" / "skills" / "demo" / "leak").symlink_to(secrets)
        cache = tmp_path / ".hermes" / "skills" / "demo" / "__pycache__"
        cache.mkdir()
        _ = (cache / "mod.cpython-312.pyc").write_bytes(b"\x00")
        dest = tmp_path / "out.tar.gz"
        packed = sb.build_archive(tmp_path, sb.DEFAULT_INCLUDE, dest)
        assert packed == 2
        with tarfile.open(dest) as archive:
            names = archive.getnames()
        assert not any("leak" in n or "__pycache__" in n for n in names)

    def test_c_missing_include_dirs_are_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".hermes").mkdir()
        dest = tmp_path / "out.tar.gz"
        assert sb.build_archive(tmp_path, sb.DEFAULT_INCLUDE, dest) == 0


class TestEncrypt:
    def _refuse_runner(self, *args, **kwargs):
        raise AssertionError("openssl must not run")

    def test_a_missing_key_fails_before_openssl(self, tmp_path: Path) -> None:
        plain = tmp_path / "a.tar.gz"
        _ = plain.write_bytes(b"data")
        with pytest.raises(sb.BackupError, match="BACKUP-KEY-MISSING"):
            sb.encrypt_archive(
                plain, sb.backup_key_path(tmp_path), tmp_path / "a.enc",
                runner=self._refuse_runner,
            )

    def test_b_exposed_key_fails_before_openssl(self, tmp_path: Path) -> None:
        plain = tmp_path / "a.tar.gz"
        _ = plain.write_bytes(b"data")
        key = _seed_key(tmp_path)
        key.chmod(0o644)
        with pytest.raises(sb.BackupError, match="BACKUP-KEY-EXPOSED"):
            sb.encrypt_archive(plain, key, tmp_path / "a.enc", runner=self._refuse_runner)

    def test_c_argv_binds_pbkdf2_and_keyfile(self, tmp_path: Path) -> None:
        plain = tmp_path / "a.tar.gz"
        _ = plain.write_bytes(b"data")
        key = _seed_key(tmp_path)
        seen: list[list[str]] = []

        def runner(argv, **kwargs):
            seen.append(list(argv))
            return _fake_openssl(argv)

        sb.encrypt_archive(plain, key, tmp_path / "a.enc", runner=runner)
        (argv,) = seen
        assert argv[0] == "openssl"
        assert "-pbkdf2" in argv
        assert f"file:{key}" in argv

    def test_d_nonzero_or_empty_output_is_an_error(self, tmp_path: Path) -> None:
        plain = tmp_path / "a.tar.gz"
        _ = plain.write_bytes(b"data")
        key = _seed_key(tmp_path)

        def failing(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, b"", b"bad")

        with pytest.raises(sb.BackupError, match="BACKUP-ENCRYPT-FAIL"):
            sb.encrypt_archive(plain, key, tmp_path / "a.enc", runner=failing)


class TestRunOnce:
    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")

    def _run(self, home: Path, client: FakeClient) -> int:
        return sb.run_once(
            home=home, client=client, now=NOW, runner=_fake_openssl,
            account_label="lab",
        )

    def test_a_happy_path_uploads_sealed_bytes_then_marks(self, tmp_path: Path) -> None:
        _seed_state(tmp_path)
        _ = _seed_key(tmp_path)
        client = FakeClient()
        assert self._run(tmp_path, client) == 0
        kinds = [call[0] for call in client.calls]
        assert kinds == ["ensure", "upsert", "owner", "readback", "list"]
        ensure = client.calls[0]
        assert ensure[1] == (sb.BACKUP_ROOT_FOLDER, "lab")
        _tag, name, _parent, head = client.calls[1]
        assert name == "state-lab-2026-W36.tar.enc"
        assert head.startswith(b"Salted__")  # 평문 gzip(\x1f\x8b)이 아니다
        raw = json.loads(sb.watermark_path(tmp_path).read_text())
        assert raw == {"delivered_week": "2026-W36"}

    def test_b_second_tick_same_week_is_silent_noop(self, tmp_path: Path) -> None:
        _seed_state(tmp_path)
        _ = _seed_key(tmp_path)
        assert self._run(tmp_path, FakeClient()) == 0
        second = FakeClient()
        assert self._run(tmp_path, second) == 0
        assert second.calls == []

    def test_c_verify_failure_keeps_watermark_for_retry(self, tmp_path: Path) -> None:
        _seed_state(tmp_path)
        _ = _seed_key(tmp_path)

        class Failing(FakeClient):
            def verify_owner_only(self, file_id):
                raise RuntimeError("owner check failed")

        with pytest.raises(RuntimeError):
            _ = self._run(tmp_path, Failing())
        assert not sb.watermark_path(tmp_path).exists()

    def test_d_retention_trashes_only_matching_old_generations(
        self, tmp_path: Path
    ) -> None:
        _seed_state(tmp_path)
        _ = _seed_key(tmp_path)
        client = FakeClient()
        client.children = [
            {"name": f"state-lab-2026-W{25 + i}.tar.enc", "id": f"g{i}"}
            for i in range(10)
        ] + [{"name": "unrelated.txt", "id": "u1"}]
        assert self._run(tmp_path, client) == 0
        trashed = {call[1] for call in client.calls if call[0] == "trash"}
        assert trashed == {"g0", "g1"}  # 최신 8세대 보존, 무관 파일 불가침

    def test_e_missing_key_fails_closed_without_upload(self, tmp_path: Path) -> None:
        _seed_state(tmp_path)
        client = FakeClient()
        with pytest.raises(sb.BackupError, match="BACKUP-KEY-MISSING"):
            _ = self._run(tmp_path, client)
        assert client.calls == []
        assert not sb.watermark_path(tmp_path).exists()

    def test_f_optin_unset_skips_without_touching_drive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
        client = FakeClient()
        assert self._run(tmp_path, client) == 0
        assert client.calls == []
        assert "DRIVE-PUBLISH-DISABLED" in capsys.readouterr().err

    def test_g_empty_state_skips_without_upload(self, tmp_path: Path) -> None:
        (tmp_path / ".hermes").mkdir()
        _ = _seed_key(tmp_path)
        client = FakeClient()
        assert self._run(tmp_path, client) == 0
        assert client.calls == []
        assert not sb.watermark_path(tmp_path).exists()


class TestDeploySurfaces:
    """워처 래퍼·배포기가 no-agent cron 규약을 지키는지 텍스트로 고정한다."""

    def test_a_wrapper_follows_cron_conventions(self) -> None:
        text = (
            REPO_ROOT / "automation" / "state_backup" / "cron" / "state_backup_watch.py"
        ).read_text(encoding="utf-8")
        assert '".env.secrets"' in text  # 규약 (b): 시크릿 자가 로드
        assert "AUTOPHAGY_REPO_ROOT" in text  # 규약 (b-2): 자식 env 명시 전파
        assert "automation.state_backup.backup" in text
        assert "/srv/autophagy-agent-current" in text  # 릴리스 우선 런타임 루트

    def test_b_deployer_declares_manifest_and_provenance(self) -> None:
        root = REPO_ROOT / "automation" / "state_backup"
        deploy = (root / "deploy.sh").read_text(encoding="utf-8")
        assert "deploy_provenance_check" in deploy
        assert "state-backup-watch" in deploy
        manifest = (root / "deploy-manifest.txt").read_text(encoding="utf-8")
        assert (
            "agent|automation/state_backup/cron/state_backup_watch.py"
            "|.hermes/scripts/state_backup_watch.py|required" in manifest
        )
