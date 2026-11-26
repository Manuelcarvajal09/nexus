<#
.SYNOPSIS
  Creates a Nexus shortcut that starts the app without any console window and
  shows the Nexus icon (instead of cmd.exe's).

.DESCRIPTION
  Writes ONE .lnk at the path you give. It never touches the Desktop, Start
  Menu, Startup folder or taskbar by itself: choose the destination explicitly.
  Target: <project>\.venv\Scripts\pythonw.exe -m nexus.platform.windows.app
  Icon:   <project>\src\nexus\ui\assets\icons\nexus.ico

  With -CodeRoot the app runs from that folder's "src" (for example a
  `git archive` copy of a release) instead of the editable project: the same
  interpreter, but the code of that folder. The icon is taken from there too.

.EXAMPLE
  .\windows_make_shortcut.ps1 -Path "$env:TEMP\Nexus-test.lnk"
.EXAMPLE
  .\windows_make_shortcut.ps1 -Path "$env:TEMP\Nexus-v1.lnk" -CodeRoot "$env:LOCALAPPDATA\Nexus\app\v1"
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$ProjectRoot,
    [string]$CodeRoot,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$python = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
$iconRoot = if ($CodeRoot) { $CodeRoot } else { $ProjectRoot }
$icon = Join-Path $iconRoot 'src\nexus\ui\assets\icons\nexus.ico'
$required = @($python)
if ($CodeRoot) { $required += (Join-Path $CodeRoot 'src\nexus\platform\windows\app.py') }
if (Test-Path -LiteralPath $icon) { $useIcon = $true } else { $useIcon = $false }
foreach ($item in $required) {
    if (-not (Test-Path -LiteralPath $item)) { throw "No existe: $item" }
}
if ((Test-Path -LiteralPath $Path) -and -not $Force) { throw "Ya existe $Path; usa -Force para reemplazarlo." }
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
$shortcut.TargetPath = $python
if ($CodeRoot) {
    $src = Join-Path (Resolve-Path -LiteralPath $CodeRoot).Path 'src'
    $shortcut.Arguments = "-c `"import sys, runpy; sys.path.insert(0, r'$src'); runpy.run_module('nexus.platform.windows.app', run_name='__main__')`""
    $shortcut.WorkingDirectory = (Resolve-Path -LiteralPath $CodeRoot).Path
} else {
    $shortcut.Arguments = '-m nexus.platform.windows.app'
    $shortcut.WorkingDirectory = $ProjectRoot
}
if ($useIcon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.WindowStyle = 1
$shortcut.Description = 'Nexus'
$shortcut.Save()
Write-Output "Creado: $Path"
