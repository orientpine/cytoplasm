"""설치 래퍼의 실제 자식 환경과 배포 등록의 멱등성을 오프라인에서 검증한다."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "automation/stt_eval"


def executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return path


@pytest.mark.parametrize("existing", [False, True])
def test_installed_wrapper_loads_secrets_and_forwards_to_drive_child(tmp_path: Path, existing: bool) -> None:
    installed = tmp_path / ".hermes/scripts/stt_eval_capture_watch.py"
    installed.parent.mkdir(parents=True)
    _ = shutil.copyfile(PACKAGE / "cron/stt_eval_capture_watch.py", installed)
    gws = executable(tmp_path / "gws", (
        "import json, os\nfrom pathlib import Path\n"
        "assert os.environ['CAPTURE_FIXTURE_CREDENTIAL'] == os.environ['FIXTURE_EXPECTED']\n"
        "assert os.environ['AUTOPHAGY_REPO_ROOT'] == os.environ['AUTOPHAGY_RUNTIME_ROOT']\n"
        "Path(os.environ['HOME'], 'credential-receipt').write_text('CHILD-ENV-OK')\n"
        "print(json.dumps({'files': []}))\n"
    ))
    _ = (tmp_path / ".env.secrets").write_text(
        "CAPTURE_FIXTURE_CREDENTIAL='fixture-file'\n"
        + f"AUTOPHAGY_RUNTIME_ROOT={REPO}\nDRIVE_PUBLISH_ENABLED=1\nDRIVE_GWS_BIN={gws}\n"
    )
    env = {key: value for key, value in os.environ.items() if key not in {
        "CAPTURE_FIXTURE_CREDENTIAL", "AUTOPHAGY_REPO_ROOT", "AUTOPHAGY_RUNTIME_ROOT", "DRIVE_PUBLISH_ENABLED", "DRIVE_GWS_BIN",
    }}
    env.update(HOME=str(tmp_path), STT_EVAL_ROOT=str(tmp_path / "eval"), FIXTURE_EXPECTED="fixture-env" if existing else "fixture-file")
    if existing:
        env["CAPTURE_FIXTURE_CREDENTIAL"] = "fixture-env"
    result = subprocess.run([sys.executable, str(installed), "--once"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert (tmp_path / "credential-receipt").read_text() == "CHILD-ENV-OK"


def deploy_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    package = repo / "automation/stt_eval"
    (package / "cron").mkdir(parents=True)
    _ = shutil.copyfile(PACKAGE / "deploy.sh", package / "deploy.sh")
    _ = shutil.copyfile(PACKAGE / "cron/stt_eval_capture_watch.py", package / "cron/stt_eval_capture_watch.py")
    _ = (repo / "automation/node_config_sh.py").write_text("print('NODE_AGENT_ACCOUNT=fixture-agent; NODE_PEER_ACCOUNT=fixture-peer; NODE_DEPLOY_SSH_HOST=fixture-host')\n")
    _ = (repo / "automation/deploy_provenance.sh").write_text('deploy_provenance_check() { printf "%s\\n" "$@" >> "$HOME/provenance-receipt"; return "${FIXTURE_GUARD_RC:-0}"; }\n')
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    _ = (home / ".hermes/config.yaml").write_text("timezone: Asia/Seoul\n")
    bin_dir = tmp_path / "bin"
    _ = executable(bin_dir / "ssh", (
        "import os, subprocess, sys\nfrom pathlib import Path\n"
        "assert sys.argv[1] == 'fixture-host'\n"
        "prefix = 'sudo -n -u fixture-agent -H bash -lc '\n"
        "assert sys.argv[2].startswith(prefix)\n"
        "command = sys.argv[2][len(prefix):]\n"
        "result = subprocess.run(['bash', '-c', 'eval ' + command], env=dict(os.environ), check=False)\n"
        "sys.exit(result.returncode)\n"
    ))
    _ = executable(home / ".local/bin/hermes", (
        "import json, os, sys\nfrom pathlib import Path\n"
        "state = Path(os.environ['HOME'], 'cron-receipt.json')\n"
        "args = sys.argv[1:]\n"
        "if args == ['cron', 'list', '--all']:\n"
        "    print('Name: stt-eval-capture' if state.exists() else '')\n"
        "else:\n"
        "    assert args[:2] == ['cron', 'create']\n"
        "    assert not state.exists()\n"
        "    state.write_text(json.dumps(args))\n"
    ))
    return package / "deploy.sh", {**os.environ, "HOME": str(home), "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "DEPLOY_SSH_HOST": "fixture-host"}


def test_deploy_registers_once_and_preserves_provenance_guard(tmp_path: Path) -> None:
    script, env = deploy_fixture(tmp_path)
    for _ in range(2):
        result = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
    home = Path(env["HOME"])
    assert json.loads((home / "cron-receipt.json").read_text()) == [
        "cron", "create", "40 3 * * *", "--name", "stt-eval-capture", "--no-agent", "--script", "stt_eval_capture_watch.py", "--deliver", "local",
    ]
    target = home / ".hermes/scripts/stt_eval_capture_watch.py"
    assert target.read_bytes() == (PACKAGE / "cron/stt_eval_capture_watch.py").read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert (home / "provenance-receipt").read_text().splitlines() == [str(script.parents[2]), str(script.parent)] * 2


def test_deploy_guard_refusal_precedes_install(tmp_path: Path) -> None:
    script, env = deploy_fixture(tmp_path)
    env["FIXTURE_GUARD_RC"] = "1"
    result = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 4
    assert not (Path(env["HOME"]) / ".hermes/scripts").exists()
    assert not (Path(env["HOME"]) / "cron-receipt.json").exists()
