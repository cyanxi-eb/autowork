[CmdletBinding()]
param([Parameter(Mandatory)][ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+$')][string]$Version)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$release = Join-Path $project ".orchestrator\releases\$Version"
$staging = Join-Path $release ([guid]::NewGuid().ToString('N'))
$bundle = Join-Path $staging 'AutoWork'
$runtime = Join-Path $bundle 'runtime'
$cache = Join-Path $project '.orchestrator\release-cache'
$pythonVersion = '3.13.17'
$pythonHash = '9E35319BFFEDAB8B36AD44D8F377CB4F1733BCEC257A0B6D04D94F01486DDB38'
$archiveName = "python-$pythonVersion-embed-amd64.zip"
$pythonArchive = Join-Path $cache $archiveName

New-Item -ItemType Directory -Path $bundle, $cache -Force | Out-Null
if (-not (Test-Path -LiteralPath $pythonArchive) -or (Get-FileHash -LiteralPath $pythonArchive).Hash -ne $pythonHash) {
    $download = "$pythonArchive.download"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$pythonVersion/$archiveName" -OutFile $download -TimeoutSec 60
    if ((Get-FileHash -LiteralPath $download).Hash -ne $pythonHash) { throw 'Python runtime package checksum mismatch.' }
    Move-Item -LiteralPath $download -Destination $pythonArchive -Force
}

& (Join-Path $PSScriptRoot 'Build.ps1') -OutputDirectory $bundle
[IO.Compression.ZipFile]::ExtractToDirectory($pythonArchive, $runtime)
Set-Content -LiteralPath (Join-Path $runtime 'python313._pth') -Value @('python313.zip', '.', '..') -Encoding ascii

$backend = Join-Path $bundle 'orchestrator'
$web = Join-Path $backend 'web'
New-Item -ItemType Directory -Path $web -Force | Out-Null
foreach ($file in Get-ChildItem -LiteralPath (Join-Path $project 'orchestrator') -File -Filter '*.py') {
    Copy-Item -LiteralPath $file.FullName -Destination $backend
}
foreach ($name in @('index.html', 'app.js', 'markdown.js', 'style.css')) {
    Copy-Item -LiteralPath (Join-Path $project "orchestrator\web\$name") -Destination $web
}
foreach ($agent in @('deepseek', 'dsh', 'codex')) {
    $persona = Join-Path $backend "personas\$agent"
    New-Item -ItemType Directory -Path $persona -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $project "orchestrator\personas\$agent\SOUL.md") -Destination $persona
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README-Release.md') -Destination (Join-Path $bundle 'README.md')

$zipName = "AutoWork-$Version-Windows-x64.zip"
$zip = Join-Path $release $zipName
$pending = Join-Path $staging $zipName
[IO.Compression.ZipFile]::CreateFromDirectory($bundle, $pending, [IO.Compression.CompressionLevel]::Optimal, $true)
Move-Item -LiteralPath $pending -Destination $zip -Force
$sum = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$zip.sha256" -Value "$sum  $zipName" -Encoding ascii
Write-Output $zip
Write-Output "$zip.sha256"
