[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$build = [IO.Path]::GetFullPath($OutputDirectory)
$sdk = Join-Path $project '.orchestrator\desktop\sdk'
$sdkVersion = '1.0.4314.35'
$sdkHash = '90778DEB1B77E36292828B4B5833C9255C27533F40E0DDD55891AE718944EDC4'
$package = Join-Path $sdk "microsoft.web.webview2.$sdkVersion.nupkg"
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$exe = Join-Path $build 'AutoWork.exe'
$iconFile = Join-Path $build 'AutoWork.ico'

if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows x64 .NET Framework compiler is missing.' }
if (Get-Process -Name 'AutoWork' -ErrorAction SilentlyContinue | Where-Object Path -EQ $exe) {
    throw 'Close the AutoWork window before rebuilding its desktop launcher.'
}
New-Item -ItemType Directory -Path $build, $sdk -Force | Out-Null
if (-not (Test-Path -LiteralPath $package) -or (Get-FileHash -LiteralPath $package).Hash -ne $sdkHash) {
    $download = "$package.download"
    $packageUrl = "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$sdkVersion/microsoft.web.webview2.$sdkVersion.nupkg"
    Invoke-WebRequest -Uri $packageUrl -OutFile $download -TimeoutSec 60
    if ((Get-FileHash -LiteralPath $download).Hash -ne $sdkHash) { throw 'WebView2 SDK package checksum mismatch.' }
    Move-Item -LiteralPath $download -Destination $package -Force
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.Drawing
$archive = [IO.Compression.ZipFile]::OpenRead($package)
try {
    foreach ($member in @('lib/net462/Microsoft.Web.WebView2.Core.dll', 'lib/net462/Microsoft.Web.WebView2.WinForms.dll', 'runtimes/win-x64/native/WebView2Loader.dll')) {
        $entry = $archive.GetEntry($member)
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $build $entry.Name), $true)
    }
    $licenses = Join-Path $build 'licenses'
    New-Item -ItemType Directory -Path $licenses -Force | Out-Null
    foreach ($name in @('LICENSE.txt', 'NOTICE.txt')) {
        [IO.Compression.ZipFileExtensions]::ExtractToFile($archive.GetEntry($name), (Join-Path $licenses "WebView2-$name"), $true)
    }
} finally {
    $archive.Dispose()
}

$bitmap = [Drawing.Bitmap]::new(48, 48)
$canvas = [Drawing.Graphics]::FromImage($bitmap)
$canvas.Clear([Drawing.Color]::FromArgb(23, 46, 94))
$blue = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(156, 186, 255))
$canvas.FillRectangle($blue, 11, 19, 5, 11)
$canvas.FillRectangle([Drawing.Brushes]::White, 21, 11, 6, 26)
$canvas.FillRectangle($blue, 32, 19, 5, 11)
$icon = [Drawing.Icon]::FromHandle($bitmap.GetHicon())
$stream = [IO.File]::Create($iconFile)
try { $icon.Save($stream) } finally {
    $stream.Dispose()
    $icon.Dispose()
    $blue.Dispose()
    $canvas.Dispose()
    $bitmap.Dispose()
}

$core = Join-Path $build 'Microsoft.Web.WebView2.Core.dll'
$forms = Join-Path $build 'Microsoft.Web.WebView2.WinForms.dll'
& $compiler /nologo /target:winexe /platform:x64 /codepage:65001 "/out:$exe" "/win32icon:$iconFile" `
    /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll "/reference:$core" "/reference:$forms" `
    (Join-Path $PSScriptRoot 'AutoWork.cs')
if ($LASTEXITCODE -ne 0) { throw 'AutoWork compilation failed.' }
