[CmdletBinding()]
param([switch]$Open)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$build = Join-Path $project '.orchestrator\desktop'
$exe = Join-Path $build 'AutoWork.exe'
$pythonPath = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source

& (Join-Path $PSScriptRoot 'Build.ps1') -OutputDirectory $build
Set-Content -LiteralPath (Join-Path $build 'python-path.txt') -Value $pythonPath -Encoding utf8

$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop)) { throw 'The current user desktop directory is unavailable.' }
$shortcutPath = Join-Path $desktop 'AutoWork.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -ne $exe) {
    throw "An unrelated shortcut already exists at $shortcutPath."
}
$shortcut.TargetPath = $exe
$shortcut.IconLocation = "$exe,0"
$shortcut.WorkingDirectory = $project
$shortcut.Description = 'AutoWork desktop workbench'
$shortcut.Save()
Write-Output "Desktop shortcut: $shortcutPath"
Write-Output "Application: $exe"
if ($Open) { Start-Process -FilePath $exe -WorkingDirectory $project }
