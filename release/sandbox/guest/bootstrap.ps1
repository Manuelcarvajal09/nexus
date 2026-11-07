<#
  Runs INSIDE Windows Sandbox (LogonCommand). Order matters:
    1. isolation checks  -> if ANY fails, nothing else runs (no copy, no paste, no install)
    2. install the RC package offline into C:\Nexus-RC1 (rc1_manage.py verifies hashes first)
    3. functional tests with synthetic data only
  Everything is written to C:\Results\<timestamp>\ (the only writable mapping).
#>
$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format yyyyMMddTHHmmss
$out = "C:\Results\$stamp"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out 'bootstrap.log'
function Log($m) { $line = "$(Get-Date -Format o) $m"; $line | Tee-Object -FilePath $log -Append }

# ---------------------------------------------------------------- 1. isolation
$checks = [ordered]@{}
function Check($name, [scriptblock]$test) {
    try { $ok = [bool](& $test) } catch { $ok = $false }
    $checks[$name] = $ok
    Log ("{0,-48} {1}" -f $name, $(if ($ok) { 'PASS' } else { 'FAIL' }))
}

Check 'running as the sandbox account (WDAGUtilityAccount)' { $env:USERNAME -eq 'WDAGUtilityAccount' }
Check 'no host user profile exists in this machine'           {
    $known = 'WDAGUtilityAccount', 'Public', 'Default', 'Default User', 'All Users', 'desktop.ini'
    @(Get-ChildItem 'C:\Users' -Force | Where-Object { $known -notcontains $_.Name }).Count -eq 0
}
Check 'no pre-existing Nexus data in this profile'          { -not (Test-Path "$env:LOCALAPPDATA\Nexus") }
Check 'sandbox clipboard starts empty'                      { [string]::IsNullOrWhiteSpace((Get-Clipboard -Raw)) }
Check 'no network: TCP connect to 1.1.1.1:443 fails'        {
    $c = New-Object Net.Sockets.TcpClient
    $iar = $c.BeginConnect('1.1.1.1', 443, $null, $null)
    $connected = $iar.AsyncWaitHandle.WaitOne(3000) -and $c.Connected
    $c.Close(); -not $connected
}
Check 'no network: DNS lookup fails'                        {
    try { Resolve-DnsName example.com -ErrorAction Stop | Out-Null; $false } catch { $true }
}
Check 'repository is read-only (cannot create a file)'      {
    try { New-Item -ItemType File 'C:\Repo\__write_test__' -ErrorAction Stop | Out-Null; Remove-Item 'C:\Repo\__write_test__'; $false } catch { $true }
}
Check 'host Python folder is read-only'                     {
    try { New-Item -ItemType File 'C:\HostPython\__write_test__' -ErrorAction Stop | Out-Null; Remove-Item 'C:\HostPython\__write_test__'; $false } catch { $true }
}
Check 'results folder is writable'                          { New-Item -ItemType File "$out\__w" -Force | Out-Null; Remove-Item "$out\__w"; $true }
Check 'host Python runs'                                    { (& 'C:\HostPython\python.exe' --version) -match '^Python 3\.11' }
Check 'RC package present and read-only'                    { Test-Path 'C:\Repo\dist-rc1\SHA256SUMS' }

# Reverse-direction sentinel: the sandbox clipboard gets a unique marker. If clipboard sharing were on, it would
# show up on the host. The host-side check is manual and optional (see release/sandbox/README_SANDBOX.md).
$sentinel = "NEXUS-SANDBOX-SENTINEL-$([guid]::NewGuid().ToString('N'))"
Set-Clipboard -Value $sentinel
$sentinel | Out-File (Join-Path $out 'sentinel.txt') -Encoding utf8

$allOk = -not ($checks.Values -contains $false)
[ordered]@{ all_ok = $allOk; checks = $checks; sentinel_set_in_sandbox = $sentinel; at = (Get-Date -Format o) } |
    ConvertTo-Json | Out-File (Join-Path $out 'isolation.json') -Encoding utf8
if (-not $allOk) {
    Log 'ISOLATION CHECKS FAILED: stopping. No clipboard test, paste test or install was performed.'
    exit 1
}
Log 'isolation checks passed'
Set-Clipboard -Value ' '   # leave the clipboard neutral before tests; tests set their own synthetic content

# ---------------------------------------------------------------- 2. install (offline, hash verified)
$py = 'C:\HostPython\python.exe'
& $py 'C:\Repo\dist-rc1\tools\rc1_manage.py' install --package 'C:\Repo\dist-rc1' --root 'C:\Nexus-RC1' --allow-real-install --python $py 2>&1 | Tee-Object -FilePath (Join-Path $out 'install.log')
if ($LASTEXITCODE -ne 0) { Log 'INSTALL FAILED (see install.log): stopping'; exit 2 }

# ---------------------------------------------------------------- 3. functional tests (synthetic data only)
$state = Get-Content 'C:\Nexus-RC1\current.json' | ConvertFrom-Json
$venvPy = "C:\Nexus-RC1\versions\$($state.current)\venv\Scripts\python.exe"
& $venvPy 'C:\Repo\release\sandbox\guest\functional_tests.py' --results $out --isolation-report (Join-Path $out 'isolation.json') 2>&1 |
    Tee-Object -FilePath (Join-Path $out 'functional.log')
Log "functional tests finished with exit code $LASTEXITCODE (see functional_results.json)"
