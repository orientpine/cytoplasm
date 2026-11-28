# End-to-end check of the native Windows install, always-on tasks and update, run by CI (windows-install job) on a
# clean windows-latest machine and runnable by hand on a spare Windows account. It installs into the real profile of the
# account it runs as (~\.cytoplasm, ~\.dori, ~\.agents, ~\Documents, HKCU PATH, two Task Scheduler entries).
#
# The release channel is a local bare clone of this checkout whose tags are signed by a key made here; nothing of the
# owner's is used. The fork is fetched from GitHub at the pinned commit, and the update moves it to $NewForkPin.
#   pwsh -File tests/windows/install-update.ps1 [-Evidence <dir>]
param(
  [string]$Evidence = $(if ($env:RUNNER_TEMP) { Join-Path $env:RUNNER_TEMP "cyto-windows-evidence" } else { Join-Path ([IO.Path]::GetTempPath()) "cyto-windows-evidence" }),
  [string]$NewForkPin = "8ae06133705ce0e8edc9edb7f604a1ed18fd5869"
)
$ErrorActionPreference = "Stop"
$script:Server = $null
trap { if ($script:Server) { $script:Server.Stop() }; break }
$PSNativeCommandArgumentPassing = "Standard"
$checkout = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$work = Join-Path ([IO.Path]::GetTempPath()) ("cyto-win-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $Evidence, $work | Out-Null
$root = Join-Path $env:USERPROFILE ".cytoplasm"
$git = @("-c", "user.name=ci", "-c", "user.email=ci@example.invalid", "-c", "commit.gpgsign=false")

function Native($command, [string[]]$arguments) {
  & $command @arguments
  if ($LASTEXITCODE -ne 0) { throw "FAIL: $command $($arguments -join ' ') (exit $LASTEXITCODE)" }
}
function Check($ok, $what) {
  if (-not $ok) { throw "FAIL: $what" }
  Write-Host "PASS: $what"
  Add-Content -LiteralPath (Join-Path $Evidence "verdicts.txt") -Value "PASS: $what"
}
function FreshPath {
  $env:Path = @([Environment]::GetEnvironmentVariable("Path", "Machine"), [Environment]::GetEnvironmentVariable("Path", "User")) -join ";"
}
function ServeInstaller {
  # README's line fetches install.ps1 with irm; this serves the checkout's file over loopback HTTP the way
  # raw.githubusercontent.com does (text/plain; charset=utf-8), so the line runs here as written
  $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  $listener.Start()
  # the thread job's accept loop would keep pwsh alive after the last line: the trap below and the end stop it
  $script:Server = $listener
  $null = Start-ThreadJob -ArgumentList $listener, ([IO.File]::ReadAllBytes((Join-Path $checkout "install.ps1"))) -ScriptBlock {
    param($listener, $body)
    while ($true) {
      try { $client = $listener.AcceptTcpClient() } catch { return }
      try {
        $stream = $client.GetStream()
        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::ASCII, $false, 4096, $true)
        while ($reader.ReadLine()) { }
        $head = [Text.Encoding]::ASCII.GetBytes("HTTP/1.1 200 OK`r`nContent-Type: text/plain; charset=utf-8`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n")
        $stream.Write($head, 0, $head.Length); $stream.Write($body, 0, $body.Length); $stream.Flush()
      } finally { $client.Close() }
    }
  }
  "http://127.0.0.1:$($listener.LocalEndpoint.Port)/install.ps1"
}
function Installer([string]$log, [hashtable]$vars, [string]$line = "powershell -ExecutionPolicy Bypass -c `"irm $url | iex`"") {
  # README's line typed into a Windows PowerShell 5.1 window (the default: quoted, irm | iex), options as env vars
  $saved = @{}
  foreach ($k in $vars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $vars[$k]) }
  try {
    Write-Host "PS> $line"
    # -NonInteractive: a split run of an unfixed install.ps1 reaches Get-Content with no path, which would wait for input
    & powershell -NoProfile -NonInteractive -Command $line *>&1 | Tee-Object -FilePath (Join-Path $Evidence $log) | Out-Host
    return $LASTEXITCODE
  } finally {
    foreach ($k in $vars.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
  }
}
function Sign($repo, $tag) {
  Native "git" (@("-C", $repo) + $git + @("-c", "gpg.format=ssh", "-c", "user.signingkey=$key", "tag", "-s", $tag, "-m", $tag))
}
function ServeSupervisors {
  @(Get-CimInstance Win32_Process -Filter "Name='bun.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine -match "cli\.ts`"? serve( |$)" })
}

Write-Host "== test release channel"
$key = Join-Path $work "k"
Native "ssh-keygen" @("-q", "-t", "ed25519", "-N", "", "-C", "update-trust@autophagy", "-f", $key)
$pubLine = (Get-Content "$key.pub").Trim()
$blob = [Convert]::FromBase64String(($pubLine -split "\s+")[1])
$fp = "SHA256:" + [Convert]::ToBase64String([Security.Cryptography.SHA256]::Create().ComputeHash($blob)).TrimEnd("=")
$origin = Join-Path $work "origin.git"
$stage = Join-Path $work "stage"
Native "git" @("init", "-q", "--bare", "-b", "main", $origin)
Native "git" @("-C", $checkout, "push", "-q", $origin, "HEAD:refs/heads/main")
Native "git" @("clone", "-q", $origin, $stage)
Sign $stage "v900.0.0"
Native "git" @("-C", $stage, "push", "-q", "origin", "HEAD:refs/heads/main", "refs/tags/v900.0.0")
$pin = ((Get-Content (Join-Path $checkout "dori\pin.toml")) | Where-Object { $_ -match '^commit = "([0-9a-f]{40})"' } | ForEach-Object { $Matches[1] })
Write-Host "pinned fork $pin, update target $NewForkPin, key $fp"
$url = ServeInstaller
Write-Host "install.ps1 served at $url"

Write-Host "== S0: a Korean console (CP949): what the Dori installer reads back from PowerShell does not end it"
# a Korean PC writes PowerShell's messages in CP949 while install.ps1 sets PYTHONUTF8; the runner's own messages are
# English, so the probe writes the Korean line itself and only ASCII comes back here
$probe = Join-Path $work "cp949.py"
[IO.File]::WriteAllText($probe, @'
import subprocess
from automation.install import dori_herdr
page = subprocess.run(("powershell", "-NoProfile", "-Command", "[Console]::OutputEncoding.CodePage"), capture_output=True).stdout
print("CP949-PAGE", page.decode("ascii", "replace").strip())
said = "\uc791\uc5c5\uc744 \ub4f1\ub85d\ud558\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4"
code, out = dori_herdr.default_capture(dori_herdr.powershell(f"[Console]::Error.WriteLine('{said}'); Write-Output 'CP949-MARK'; exit 1"))
print("CP949-CAPTURE", code, "CP949-MARK" in out, said in out)
'@, [Text.UTF8Encoding]::new($false))
$env:PYTHONUTF8 = "1"; $env:PYTHONPATH = $checkout
try { $said = & cmd.exe /d /c chcp 949 '>nul' '&&' python $probe 2>&1 | Out-String } finally { Remove-Item Env:PYTHONUTF8, Env:PYTHONPATH }
$said | Set-Content -LiteralPath (Join-Path $Evidence "cp949-capture.log")
Write-Host $said
Check ($said -match "CP949-PAGE 949") "S0: a child PowerShell of this console writes CP949 (chcp 949)"
Check ($said -match "CP949-CAPTURE 1 True True") "S0: the capture keeps the exit code, the ASCII marker and the Korean message"

Write-Host "== S4a: a key whose fingerprint is not the published one runs nothing"
$code = Installer "install-wrong-fingerprint.log" @{ CYTO_REPO = $origin; CYTO_HOME = (Join-Path $work "wrong"); CYTO_TRUST_KEY = "$key.pub"; CYTO_EXPECT_FINGERPRINT = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" }
Check ($code -ne 0) "wrong fingerprint: installer exits non-zero ($code)"
Check ((Get-Content (Join-Path $Evidence "install-wrong-fingerprint.log") -Raw) -match "CYTO-WINDOWS-FINGERPRINT-MISMATCH") "wrong fingerprint: CYTO-WINDOWS-FINGERPRINT-MISMATCH printed"
Check (-not (Test-Path (Join-Path $work "wrong\src\automation"))) "wrong fingerprint: no file of the clone was checked out"

Write-Host "== S4c: the same, typed without its double quotes (the outer window's iex gets install.ps1 line by line)"
$code = Installer "install-split-wrong-fingerprint.log" @{ CYTO_REPO = $origin; CYTO_HOME = (Join-Path $work "wrong-split"); CYTO_TRUST_KEY = "$key.pub"; CYTO_EXPECT_FINGERPRINT = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"; CYTO_INSTALL_URL = $url } "powershell -ExecutionPolicy Bypass -c irm $url | iex"
$log = Get-Content (Join-Path $Evidence "install-split-wrong-fingerprint.log") -Raw
Check ($log -notmatch "ParserError|Missing '\)'") "split line, wrong fingerprint: no parse error"
Check ($log -match "CYTO-WINDOWS-FINGERPRINT-MISMATCH") "split line, wrong fingerprint: the whole script ran up to the fingerprint check"

Write-Host "== S1: install through iex, as README's line"
$code = Installer "install.log" @{ CYTO_REPO = $origin; CYTO_TRUST_KEY = "$key.pub"; CYTO_EXPECT_FINGERPRINT = $fp; CYTO_OWNER_CALL = "boss" }
$log = Get-Content (Join-Path $Evidence "install.log") -Raw
Check ($code -eq 0) "install exits 0"
Check ($log -match "CYTO-RELEASE v900\.0\.0 \(signature checked\)") "install: newest signed tag v900.0.0 chosen before any clone code ran"
Check ($log -match "CYTO-UPDATED v900\.0\.0 rc=0") "install: automation.install.update finished"
Check ($log -match "DORI-INSTALLED pin=$($pin.Substring(0, 12))") "install: Dori installer finished on the pinned fork"
Check ($log -match "CYTO-WINDOWS-INSTALLED") "install: CYTO-WINDOWS-INSTALLED printed"
Check ((& git -C (Join-Path $root "src") rev-parse HEAD) -eq (& git -C $stage rev-parse "v900.0.0^{commit}")) "cytoplasm checkout sits on v900.0.0"
Check ((& git -C (Join-Path $env:USERPROFILE ".dori\src") rev-parse HEAD) -eq $pin) "fork checkout sits on the pin $pin"
Check ((Get-Item (Join-Path $env:USERPROFILE ".agents\skills\dori-mode")).LinkType -eq "Junction") "dori-mode skill is a junction (no admin needed)"
FreshPath
$found = Get-Command dori -ErrorAction SilentlyContinue | Select-Object -First 1
$help = if ($found) { & $found.Source --help 2>&1 | Out-String } else { "dori not on PATH: $env:Path" }
$helpCode = $LASTEXITCODE
Set-Content -LiteralPath (Join-Path $Evidence "dori-help.txt") -Value "command: $($found.Source)`nexit: $helpCode`n$help"
Write-Host "dori: $($found.Source) exit $helpCode"; Write-Host ($help.Substring(0, [Math]::Min(600, $help.Length)))
# the fork's CLI prints its usage and exits 1 for --help on Linux too; the proof is that the shim runs that CLI
Check ($found -and $found.Source -like "*\.bun\bin\dori.exe" -and $help -match "dori <command>") "dori from a fresh PATH runs the fork CLI (usage printed)"
& herdr pane list | Out-Null
Check ($LASTEXITCODE -eq 0) "herdr from a fresh PATH answers (server running)"

Write-Host "== S2: always-on through the Task Scheduler"
foreach ($name in @("Dori serve", "Dori herdr server")) {
  $task = Get-ScheduledTask -TaskName $name
  $task | Format-List TaskName, State | Out-String | Add-Content -LiteralPath (Join-Path $Evidence "tasks.txt")
  $task.Triggers | Format-List | Out-String | Add-Content -LiteralPath (Join-Path $Evidence "tasks.txt")
  Check (@($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq "MSFT_TaskLogonTrigger" }).Count -eq 1) "'$name' starts at logon"
  Check ($task.Principal.RunLevel -eq "Limited") "'$name' runs without elevation"
  Check (@($task.Actions | Where-Object { $_.Arguments -match "-WindowStyle Hidden" }).Count -eq 1) "'$name' starts with a hidden window (no console left open at logon)"
}
$serveLog = Join-Path $env:USERPROFILE ".dori\state\serve.log"
for ($i = 0; $i -lt 60 -and -not ((Test-Path $serveLog) -and (Select-String -LiteralPath $serveLog -Pattern "watch SERVE_STARTED" -Quiet)); $i++) { Start-Sleep 1 }
Copy-Item $serveLog (Join-Path $Evidence "serve-after-install.log")
Check ((Get-ScheduledTask -TaskName "Dori serve").State -eq "Running") "'Dori serve' is running now"
Check (Select-String -LiteralPath $serveLog -Pattern "watch SERVE_STARTED" -Quiet) "serve.log: the watch loop started"
Check (-not (Select-String -LiteralPath $serveLog -Pattern 'Executable not found in \$PATH: "herdr"' -Quiet)) "serve.log: the loops find herdr on PATH"
$doriEnv = Join-Path $env:USERPROFILE ".dori\dori.env"
Check ((Test-Path $doriEnv) -and -not ([IO.File]::ReadAllText($doriEnv).Contains("`r"))) "dori.env written with LF line ends (the fork reads a CRLF line as unset)"
Check (-not (Select-String -LiteralPath $serveLog -Pattern 'inbound DORI_DISCORD_TOKEN is not set' -Quiet)) "serve.log: the Discord listener reads the token line of dori.env"
$before = ServeSupervisors
Check ($before.Count -eq 1) "exactly one dori serve supervisor after install ($($before.Count))"

Write-Host "== S4b: a newer tag that is not signed is not taken"
Native "git" (@("-C", $stage) + $git + @("commit", "-q", "--allow-empty", "-m", "unsigned"))
Native "git" (@("-C", $stage) + $git + @("tag", "-a", "v900.0.2", "-m", "unsigned"))
Native "git" @("-C", $stage, "push", "-q", "origin", "HEAD:refs/heads/main", "refs/tags/v900.0.2")
$code = Installer "update-unsigned.log" @{ CYTO_HOME = $root }
$log = Get-Content (Join-Path $Evidence "update-unsigned.log") -Raw
Check ($code -eq 0 -and $log -match "CYTO-UPDATE-CURRENT v900\.0\.0") "unsigned v900.0.2 skipped, stays on v900.0.0"

Write-Host "== S3: update to a signed release with a new fork pin"
Native "git" (@("-C", $stage) + $git + @("reset", "-q", "--hard", "v900.0.0"))
$pinFile = Join-Path $stage "dori\pin.toml"
[IO.File]::WriteAllText($pinFile, ([IO.File]::ReadAllText($pinFile) -replace [regex]::Escape($pin), $NewForkPin))
Native "git" (@("-C", $stage) + $git + @("commit", "-q", "-am", "pin the fork forward"))
Sign $stage "v900.0.1"
Native "git" @("-C", $stage, "push", "-q", "-f", "origin", "HEAD:refs/heads/main", "refs/tags/v900.0.1")
Native "git" @("-C", $stage, "push", "-q", "origin", ":refs/tags/v900.0.2")
$started = Get-Date
$code = Installer "update.log" @{ CYTO_HOME = $root }
$log = Get-Content (Join-Path $Evidence "update.log") -Raw
Check ($code -eq 0) "update exits 0"
Check ($log -match "CYTO-UPDATE-MOVED [0-9a-f]{12} -> v900\.0\.1") "update: cytoplasm moved to v900.0.1"
Check ($log -match "DORI-PIN-MOVED .* -> $NewForkPin") "update: fork moved to the new pin"
Check ($log -match "DORI-SERVE-TASK 'Dori serve' restarted") "update: dori serve restarted on the new code"
Check ((& git -C (Join-Path $root "src") rev-parse HEAD) -eq (& git -C $stage rev-parse "v900.0.1^{commit}")) "cytoplasm checkout sits on v900.0.1"
Check ((& git -C (Join-Path $env:USERPROFILE ".dori\src") rev-parse HEAD) -eq $NewForkPin) "fork checkout sits on $NewForkPin"
for ($i = 0; $i -lt 60 -and (ServeSupervisors).Count -ne 1; $i++) { Start-Sleep 1 }
$after = ServeSupervisors
Check ($after.Count -eq 1) "exactly one dori serve supervisor after update ($($after.Count))"
Check ($after[0].ProcessId -ne $before[0].ProcessId -and $after[0].CreationDate -gt $started) "the supervisor is a new process started by the update"
Check ((Get-ScheduledTask -TaskName "Dori serve").State -eq "Running") "'Dori serve' is running after the update"
Copy-Item $serveLog (Join-Path $Evidence "serve-after-update.log")
Write-Host "== S1b: README's line typed without its double quotes: the outer window's iex gets install.ps1 line by line"
$code = Installer "install-split.log" @{ CYTO_REPO = $origin; CYTO_TRUST_KEY = "$key.pub"; CYTO_EXPECT_FINGERPRINT = $fp; CYTO_INSTALL_URL = $url } "powershell -ExecutionPolicy Bypass -c irm $url | iex"
$log = Get-Content (Join-Path $Evidence "install-split.log") -Raw
Check ($log -notmatch "ParserError|Missing '\)'") "split line: no parse error"
Check ($log -match "CYTO-WINDOWS-WHOLE-SCRIPT") "split line: the installer noticed and ran README's line as one script"
Check ($log -match "CYTO-WINDOWS-INSTALLED") "split line: the install still finished (CYTO-WINDOWS-INSTALLED)"
Check ($log -match "CYTO-WINDOWS-WHOLE-SCRIPT-DONE: exit 0") "split line: the one-script run ended with exit 0 and says so"
Check ($log -notmatch "failed to run|NativeCommandFailed") "split line: the split copy stops quietly (no red powershell.exe line after it)"
$script:Server.Stop()
Write-Host "WINDOWS_E2E_OK"
# the split window above ends non-zero by design (its pipeline is stopped after the one-script run); every check passed, so the step does too
exit 0
