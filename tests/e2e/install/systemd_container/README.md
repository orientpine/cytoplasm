# Real installer in a systemd container

Run from the repository root on a disposable Linux Docker host:

```bash
tests/e2e/install/systemd_container/run.sh --evidence-dir /tmp/install-systemd-qa
```

Requires Docker (privileged containers and writable host cgroups), Bash, Git,
Python 3 and OpenSSH `ssh-keygen` on the host. **This is not a
security sandbox:** the privileged container has writable host cgroup access.
The image installs only the Ubuntu 24.04 packages needed by the installer and
systemd; package versions are resolved by Ubuntu apt, with the built image's
content digest recorded in the summary. The boot gate binds a notification socket
before starting the container, then awaits systemd's `READY=1` and verifies
`systemctl is-system-running`, under a shared 60-second deadline (no sleeps).

The default command is a **real apply**, not a dry run:

```bash
python3 -m automation.install --config /root/node.toml --update-trust-key /root/trust.pub
```

It proves systemd boots as PID 1 (`running` or `degraded`), accounts and linger
converge beyond the P0-5 slim-container boundary, and directories and the peer
attestation key are created. It independently checks account/linger and key
state after apply. No Hermes executable, gateway, Discord token, or credentials
are supplied or mocked: a bare container must stop at `check hermes-gateway`
(expected), or `check discord-readiness` if gateways have been provisioned.
This does **not** prove cloning, timers, final healthchecks, or signed updates.

The script returns the command's exit code: **1 is expected** for the named
external-prerequisite failure; harness boot/evidence/mutation failures use 125.
`install-transcript.txt` contains full command stdout/stderr. `summary.txt`
records the image ID (content SHA256, not a registry manifest), frozen HEAD,
command, rc, boot state, elapsed seconds, first failure, and highest action
**evidenced by results**, not merely printed in the up-front plan. Build, boot,
config notes, and independent mutation evidence are separate files in the same
directory. Reusing an evidence directory overwrites that run's files.

Only the frozen committed HEAD is archived (not local edits, `.git`, `.omo`,
`.venv`, or `.env.secrets`); do not commit real secrets. The image build receives
only the Dockerfile. The throwaway trust private key is never copied or printed
and is deleted immediately after copying its public key. No host environment
secrets are forwarded. The config uses the container hostname and root operator;
if this revision rejects empty `deploy_ssh_host`, it substitutes the same hostname
and records the parser workaround in `config-notes.txt`. No installer code changes.

Optional inspection or alternate command:

```bash
tests/e2e/install/systemd_container/run.sh --keep -- bash -c 'systemctl is-system-running; python3 -m automation.install --config /root/node.toml --update-trust-key /root/trust.pub'
```

Custom commands bypass the default post-apply mutation assertions. Their argv
and output are recorded verbatim: **never put secrets in either**. Containers are
removed on exit, including failure/signals, unless `--keep` is set; remove a kept
container and its non-secret boot-notification directory using the printed
cleanup commands. The private trust-key directory is deleted even with `--keep`.
Image-build apt traffic is allowed through Docker; the harness makes no other
network requests beyond Docker and the installer's own fetches/checks.
