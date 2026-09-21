"""설치가 남긴 것을 파일시스템·계정에서 직접 읽어, 결과 줄이 진짜 변경인지 확인한다.

컨테이너 안에서 `run.sh` 가 표준입력으로 먹인다. 개인키 내용은 읽지 않는다.

운영자가 root 가 아니면 **v1.6.1 이 흘려보낸 결함의 조건 자체**를 함께 못박는다:
배포 체크아웃은 `ops:autophagy 2750` 이고 운영자는 그 그룹에 없다. 그 조건에서만
`ProvisionHealthcheckProbe` 가 드러내던 두 결함 — 생성기 rc=126, 그리고 같은 argv 를
쓰는 수렴 판정의 영구 실패 — 이 재현된다. 손으로 만든 증적은
docs/qa/INSTALL-TUI/12-probe-asset-nonroot-operator.txt 다.
"""
from __future__ import annotations

import grp
import pwd
import stat
import subprocess
from pathlib import Path

from automation.install.assets import build_inputs
from automation.install.healthcheck_probe_asset import inspect_probe, wrapper_path
from automation.install.plan import ProvisionHealthcheckProbe
from automation.install.state import inspect_state
from automation.node_config import load_node_config

assert Path('/proc/1/comm').read_text().strip() == 'systemd'
config = load_node_config(Path('/root/node.toml'))
inputs = build_inputs(Path.cwd(), config, Path('/root/trust.pub').read_text())
state = inspect_state(inputs)
for account in ('agent', 'peer', 'ops'):
    assert account in state.ready_accounts, f'account not converged: {account}'
    linger = subprocess.check_output(['loginctl', 'show-user', account, '--property=Linger', '--value'], text=True).strip()
    assert linger == 'yes', f'linger not enabled: {account}'
    print(f'ACCOUNT-VERIFIED: {account} uid={pwd.getpwnam(account).pw_uid} Linger={linger}')
assert config.private_root in state.directories
assert config.peer_home / '.ssh' / 'peer_attest_ed25519' in state.peer_attest_keys
print('MUTATION-VERIFIED: directories and peer-attest key ownership/modes/publication')

if config.operator_account == 'root':
    raise SystemExit(0)

operator_home = state.operator_home
assert operator_home is not None, f'operator account absent: {config.operator_account}'
operator = pwd.getpwnam(config.operator_account)
assert config.operator_account not in grp.getgrnam(config.service_group).gr_mem, (
    f'operator joined {config.service_group}: 결함 조건이 재현되지 않는다'
)
checkout = config.deploy_checkout.lstat()
print(f'OPERATOR-CONDITION: {config.operator_account} uid={operator.pw_uid} not in {config.service_group}; '
      f'{config.deploy_checkout} mode={stat.S_IMODE(checkout.st_mode):#o} '
      f'owner={pwd.getpwuid(checkout.st_uid).pw_name}:{grp.getgrgid(checkout.st_gid).gr_name}')

probe = ProvisionHealthcheckProbe(config.operator_account, operator_home, config.ops_account, config.ops_home,
                                  config.ops_home / '.ssh/autophagy-healthcheck', config.deploy_checkout,
                                  config.primary_node_name)
wrapper = wrapper_path(probe)
assert wrapper.exists(), (
    f'PROBE-UNREACHED: {wrapper} 없음 — 프로브 자산까지 가려면 --stub-hermes 가 필요하다'
)
metadata = wrapper.lstat()
authorized = operator_home / '.ssh/authorized_keys'
authorized_metadata = authorized.lstat()
bindings = [line for line in authorized.read_text(encoding='utf-8').splitlines()
            if line.startswith(f'restrict,command="{wrapper}" ')]
print(f'PROBE-STATE: {wrapper} mode={stat.S_IMODE(metadata.st_mode):#o} '
      f'owner={pwd.getpwuid(metadata.st_uid).pw_name}:{grp.getgrgid(metadata.st_gid).gr_name}')
print(f'BINDING-STATE: lines={len(bindings)} {authorized} '
      f'mode={stat.S_IMODE(authorized_metadata.st_mode):#o} '
      f'owner={pwd.getpwuid(authorized_metadata.st_uid).pw_name}')
# 판정도 생성기와 같은 argv 를 쓴다. 이 한 줄이 두 번째 결함(수렴한 노드에 같은 액션이
# 매번 다시 계획되던 것)이 닫혔다는 증거이며, 래퍼·바인딩의 소유·모드를 모두 포함한다.
assert inspect_probe(probe), 'PROBE-NOT-CONVERGED: inspect_probe=False'
print('PROBE-CONVERGED: inspect_probe=True')

sudoers = Path('/etc/sudoers.d/autophagy-orchestration')
sudoers_metadata = sudoers.lstat()
grantees = {line.split(maxsplit=1)[0] for line in sudoers.read_text(encoding='utf-8').splitlines() if line.strip()}
assert grantees == {config.operator_account}, f'sudoers grantee mismatch: {sorted(grantees)}'
assert stat.S_IMODE(sudoers_metadata.st_mode) == 0o440, f'sudoers mode {stat.S_IMODE(sudoers_metadata.st_mode):#o}'
print(f'SUDOERS-VERIFIED: {sudoers} mode=0o440 grantee={config.operator_account}')
