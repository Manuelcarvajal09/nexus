<#
.SYNOPSIS
  Builds Nexus.exe (PyInstaller, one-folder) and the Nexus-Setup-<version>.exe installer (Inno Setup 6).

.DESCRIPTION
  Requirements: Python 3.11+ with PySide6-Essentials and PyInstaller (pass -Python), and Inno Setup 6
  (ISCC.exe on PATH, in %LOCALAPPDATA%\Programs\Inno Setup 6, or in Program Files).
  Output: dist-installer\Nexus-Setup-<version>.exe and its SHA-256. Temporary build folders are removed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Python C:\build\venv\Scripts\python.exe
#>
param(
    [string]$Python = "python",
    [switch]$KeepBuild
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

$version = (Select-String -Path pyproject.toml -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value
$parts = ($version -replace '[^0-9.].*$', '').Split('.') + @('0', '0', '0') | Select-Object -First 4
$tuple = ($parts -join ', ')
Write-Host "Nexus $version"

# Windows file version resource shown in Explorer > Properties > Details.
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Manuel Carvajal'),
      StringStruct('FileDescription', 'Nexus clipboard manager'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'Nexus'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Manuel Carvajal. MIT License.'),
      StringStruct('OriginalFilename', 'Nexus.exe'),
      StringStruct('ProductName', 'Nexus'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding ascii packaging\windows\version_info.txt

$work = Join-Path $root "build\pyinstaller"
& $Python -m PyInstaller --noconfirm --clean --log-level WARN `
    --distpath (Join-Path $work "dist") --workpath (Join-Path $work "work") packaging\windows\nexus.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found" }

$out = Join-Path $root "dist-installer"
New-Item -ItemType Directory -Force $out | Out-Null
& $iscc /Q "/DAppVersion=$version" "/DAppVersionNumeric=$($parts -join '.')" "/DSourceDir=$(Join-Path $work 'dist\Nexus')" "/DOutputDir=$out" packaging\windows\nexus.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$setup = Join-Path $out "Nexus-Setup-$version.exe"
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
"$hash  Nexus-Setup-$version.exe" | Set-Content -Encoding ascii "$setup.sha256"
Remove-Item packaging\windows\version_info.txt
if (-not $KeepBuild) { Remove-Item (Join-Path $root "build") -Recurse -Force }
Write-Host "Installer: $setup"
Write-Host "SHA-256:   $hash"
