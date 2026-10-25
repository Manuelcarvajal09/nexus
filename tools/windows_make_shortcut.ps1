<#
.SYNOPSIS
  Creates a Nexus shortcut that starts the app without any console window and
  shows the Nexus icon (instead of cmd.exe's).

.DESCRIPTION
  Writes ONE .lnk at the path you give. It never touches the Desktop, Start
  Menu, Startup folder or taskbar by itself: choose the destination explicitly.
  Target: <project>\.venv\Scripts\pythonw.exe -m nexus_linux.windows_app
  Icon:   <project>\src\nexus_linux\assets\icons\nexus.ico

.EXAMPLE
  .\windows_make_shortcut.ps1 -Path "$env:TEMP\Nexus-test.lnk"
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$ProjectRoot,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$python = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
$icon = Join-Path $ProjectRoot 'src\nexus_linux\assets\icons\nexus.ico'
foreach ($required in @($python, $icon)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "No existe: $required" }
}
if ((Test-Path -LiteralPath $Path) -and -not $Force) { throw "Ya existe $Path; usa -Force para reemplazarlo." }
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
$shortcut.TargetPath = $python
$shortcut.Arguments = '-m nexus_linux.windows_app'
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.IconLocation = "$icon,0"
$shortcut.WindowStyle = 1
$shortcut.Description = 'Nexus'
$shortcut.Save()
Write-Output "Creado: $Path"
