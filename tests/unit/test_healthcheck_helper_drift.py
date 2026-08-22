from __future__ import annotations

import os
import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_PROBE = _REPO / "automation" / "release_helper_probe.sh"


def _run_probe(
    installed_helper: Path,
    installed_provenance: Path,
    release_root: Path,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "HEALTHCHECK_RELEASE_HELPER": str(installed_helper),
            "HEALTHCHECK_RELEASE_PROVENANCE": str(installed_provenance),
            "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(release_root),
            # The probe also compares the reconcile unit and the gateway helper now:
            # those are installed OUTSIDE the release too, and the one that was not
            # watched is precisely the one that froze production for three days.
            "HEALTHCHECK_LIBEXEC_DIR": str(installed_helper.parent),
            "HEALTHCHECK_UNIT_DIR": str(installed_helper.parent.parent / "units"),
        }
    )
    return subprocess.run(
        (
            "bash",
            "-c",
            f'source "{_PROBE}"; probe_release_helper_drift node ops ignored',
        ),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


#: `.service` 와 `gateway_pair.py` 는 실제로는 `$NODE_*` 템플릿이라 프로브가 렌더러를
#: 거친다. 픽스처는 그 렌더러 자리에 **복사 스텁**을 놓아, 노드 config 없이도 같은
#: 코드 경로(렌더 → sha 비교)를 그대로 지나가게 한다.
_RENDERER_STUB = "import shutil, sys\nshutil.copyfile(sys.argv[1], sys.argv[2])\n"


def _matching_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    installed = tmp_path / "installed"
    units = tmp_path / "units"
    source = tmp_path / "release" / "automation"
    installed.mkdir()
    units.mkdir()
    (source / "systemd").mkdir(parents=True)
    helper = installed / "autophagy-install-release"
    provenance = installed / "release_provenance.py"
    helper.write_text("helper-v1\n", encoding="utf-8")
    provenance.write_text("provenance-v1\n", encoding="utf-8")
    (source / "release_store.py").write_bytes(helper.read_bytes())
    (source / "release_provenance.py").write_bytes(provenance.read_bytes())
    _ = (source / "node_asset_renderer.py").write_text(_RENDERER_STUB, encoding="utf-8")
    converge = installed / "autophagy-converge.d"
    converge.mkdir()
    (source / "libexec").mkdir()
    for installed_path, source_path, payload in (
        (installed / "autophagy-gateway-pair", source / "gateway_pair.py", "pair-v1\n"),
        (
            units / "autophagy-deploy-reconcile.service",
            source / "systemd" / "autophagy-deploy-reconcile.service",
            "service-v1\n",
        ),
        (
            units / "autophagy-deploy-reconcile.timer",
            source / "systemd" / "autophagy-deploy-reconcile.timer",
            "timer-v1\n",
        ),
        (installed / "autophagy-install-skill", source / "skill_store.py", "skill-v1\n"),
        (
            installed / "autophagy-converge-origin-main",
            source / "converge_origin_main.sh",
            "converge-v1\n",
        ),
        (
            installed / "autophagy-resume-deploy",
            source / "libexec" / "autophagy-resume-deploy",
            "resume-v1\n",
        ),
        (
            converge / "origin_snapshot.sh",
            source / "origin_snapshot.sh",
            "snapshot-v1\n",
        ),
    ):
        _ = installed_path.write_text(payload, encoding="utf-8")
        _ = source_path.write_text(payload, encoding="utf-8")
    # 리컨실러가 쓰는 converge.d 사본은 libexec 루트의 동명 자산과 **같은 소스**를
    # 공유하지만 별개 파일이다 — 한쪽만 신선해지는 상황이 실제로 일어난다.
    (converge / "release_store.py").write_bytes(helper.read_bytes())
    (converge / "release_provenance.py").write_bytes(provenance.read_bytes())
    return helper, provenance, source.parent


def test_matching_privileged_helpers_pass(tmp_path: Path) -> None:
    helper, provenance, release_root = _matching_files(tmp_path)

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "HELPER-DRIFT-PASS" in result.stderr


def test_changed_privileged_helper_fails_with_check_marker(tmp_path: Path) -> None:
    helper, provenance, release_root = _matching_files(tmp_path)
    helper.write_text("drifted\n", encoding="utf-8")

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode != 0
    assert "HELPER-DRIFT" in result.stderr


def test_missing_helper_is_drift_with_the_re_run_instructions(tmp_path: Path) -> None:
    """설치기가 놓기로 된 파일의 **부재**는 모르는 것이 아니라 드리프트다.

    2026-08-20 실측에서 `autophagy-converge.d/release_provenance.py` 가 아예 없었다.
    UNKNOWN 으로 묽끬면 재실행 안내가 같이 나오지 않아 고칠 방법이 안 보인다.
    """
    helper, provenance, release_root = _matching_files(tmp_path)
    helper.unlink()

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode != 0
    assert "is not installed" in result.stderr
    assert "provision-release-store.sh" in result.stderr, (
        "이 자산을 놓는 바로 그 프로비저너를 집어야 한다"
    )
    assert "HELPER-DRIFT-PASS" not in result.stderr

def test_each_asset_names_its_own_provisioner(tmp_path: Path) -> None:
    """자산마다 소유 프로비저너가 다르다 — 틀린 스크립트를 안내하면
    사람이 그걸 돌리고 드리프트는 남은 채 고쳤다고 믿게 된다."""
    helper, provenance, release_root = _matching_files(tmp_path)
    (helper.parent / "autophagy-converge.d" / "release_store.py").write_text(
        "drifted\n", encoding="utf-8"
    )

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode != 0
    assert "provision-deploy-converge.sh" in result.stderr
    assert "provision-release-store.sh" not in result.stderr, (
        "무관한 프로비저너까지 나열하면 어느 걸 돌려야 하는지 다시 모호해진다"
    )

def test_present_but_unreadable_helper_is_unknown_not_missing(tmp_path: Path) -> None:
    """반대 방향 — 권한 때문에 못 읽는 것을 '미설치'로 단언하면
    멀집한 파일을 다시 깔라고 시키는 오보가 된다(프로브는 cron 계정으로 돌아간다)."""
    helper, provenance, release_root = _matching_files(tmp_path)
    helper.chmod(0o000)

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode != 0
    assert "HELPER-DRIFT-UNKNOWN" in result.stderr
    assert "is not installed" not in result.stderr
    assert "HELPER-DRIFT-PASS" not in result.stderr


def test_helper_probe_uses_no_ssh_or_sudo() -> None:
    result = subprocess.run(
        ("bash", "-c", f'source "{_PROBE}"; declare -f probe_release_helper_drift'),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ssh" not in result.stdout
    assert "sudo" not in result.stdout


def test_a_drifted_reconcile_unit_is_caught(tmp_path: Path) -> None:
    """2026-08-19 의 실제 사고 모양 — 유닛만 낡고 헬퍼는 멀쩡하다.

    구 프로브는 이 상황을 PASS 로 통과시켰다(실측). 릴리스 트리는 자동 수렴하지만
    `/etc/systemd/system` 은 프로비저너가 root 로 설치하므로 아무도 따라오지 않는다.
    """
    helper, provenance, release_root = _matching_files(tmp_path)
    unit = tmp_path / "units" / "autophagy-deploy-reconcile.service"
    _ = unit.write_text("service-v1-without-BindPaths\n", encoding="utf-8")

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode != 0
    assert "HELPER-DRIFT" in result.stderr
    assert "provision-deploy-reconcile.sh" in result.stderr, "무엇을 하라는지 함께 말해야 한다"


def test_a_drifted_gateway_helper_is_caught(tmp_path: Path) -> None:
    helper, provenance, release_root = _matching_files(tmp_path)
    _ = (tmp_path / "installed" / "autophagy-gateway-pair").write_text("drifted\n", encoding="utf-8")

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode != 0
    assert "HELPER-DRIFT" in result.stderr
