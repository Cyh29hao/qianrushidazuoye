param(
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $repo "build_release"
$releaseDir = Join-Path $releaseRoot "SmartClockHost-v2.2"
$distDir = Join-Path $releaseRoot "_pyi_dist"
$workDir = Join-Path $releaseRoot "_pyi_build"
$specDir = Join-Path $releaseRoot "_pyi_spec"
$specPath = Join-Path $specDir "SmartClockHost.spec"
$zipPath = Join-Path $releaseRoot "SmartClockHost-v2.2.zip"
$appDataRoot = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $env:USERPROFILE "AppData\Roaming" }
$profileDir = Join-Path $appDataRoot "SmartClockHost-v2.2"
$sourceRoot = if (Test-Path "C:\smartclock_latest\pc_host\app.py") {
    "C:\smartclock_latest"
} else {
    $repo
}
$python = Join-Path $sourceRoot ".venv\Scripts\python.exe"
if (!(Test-Path $python)) {
    $python = Join-Path $sourceRoot "pc_host\.venv\Scripts\python.exe"
}
if (!(Test-Path $python)) {
    $python = Join-Path $repo ".venv\Scripts\python.exe"
}
if (!(Test-Path $python)) {
    $python = Join-Path $repo "pc_host\.venv\Scripts\python.exe"
}
if (!(Test-Path $python)) {
    throw "PyInstaller Python not found. Create .venv or pc_host\.venv first."
}

function Copy-IfMissing {
    param([string]$Source, [string]$Target)
    if ((Test-Path $Source) -and !(Test-Path $Target)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $Target -Parent) | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Target -Force
        Write-Host "Migrated legacy runtime file: $Source -> $Target"
    }
}

New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
foreach ($name in @("config.json", "runtime_state.json", "schedules.json")) {
    Copy-IfMissing (Join-Path $releaseDir $name) (Join-Path $profileDir $name)
}
Copy-IfMissing (Join-Path $releaseDir "logs\events.jsonl") (Join-Path $profileDir "logs\events.jsonl")

if (Test-Path $releaseDir) {
    Remove-Item -LiteralPath $releaseDir -Recurse -Force
}
foreach ($path in @($distDir, $workDir, $specDir)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

$assets = Join-Path $sourceRoot "pc_host\assets"
$icon = Join-Path $assets "clock_logo.ico"
$app = Join-Path $sourceRoot "pc_host\app.py"
$spec = @"
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    [r'$app'],
    pathex=[],
    binaries=[],
    datas=[(r'$assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SmartClockHost',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=r'$icon',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SmartClockHost',
)
"@
Set-Content -LiteralPath $specPath -Value $spec -Encoding UTF8

Push-Location $sourceRoot
try {
    & $python -m PyInstaller --noconfirm --clean --distpath $distDir --workpath $workDir $specPath
} finally {
    Pop-Location
}

$builtDir = Join-Path $distDir "SmartClockHost"
if (!(Test-Path $builtDir)) {
    throw "PyInstaller did not create $builtDir"
}
Move-Item -LiteralPath $builtDir -Destination $releaseDir

Copy-Item -LiteralPath (Join-Path $repo "README.md") -Destination (Join-Path $releaseDir "README.md") -Force
$releaseMcu = Join-Path $releaseDir "mcu"
New-Item -ItemType Directory -Force -Path $releaseMcu | Out-Null
foreach ($item in @("src", "Inc", "Driverlib", "RTE")) {
    $source = Join-Path $repo "mcu\$item"
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination $releaseMcu -Recurse -Force
    }
}
foreach ($file in @("clock.uvprojx", "clock.uvoptx")) {
    $source = Join-Path $repo "mcu\$file"
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $releaseMcu $file) -Force
    }
}

foreach ($name in @("config.json", "runtime_state.json", "schedules.json")) {
    $runtimeFile = Join-Path $releaseDir $name
    if (Test-Path $runtimeFile) {
        throw "Release directory must not contain runtime file: $runtimeFile"
    }
}
if (Test-Path (Join-Path $releaseDir "logs")) {
    throw "Release directory must not contain runtime logs directory."
}

if (!$SkipSmoke) {
    $smokeProfile = Join-Path $repo "tmp\release_smoke_profile_v22"
    if (Test-Path $smokeProfile) {
        Remove-Item -LiteralPath $smokeProfile -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $smokeProfile | Out-Null
    $oldProfile = $env:SMARTCLOCK_PROFILE_DIR
    $oldQt = $env:QT_QPA_PLATFORM
    try {
        $env:SMARTCLOCK_PROFILE_DIR = $smokeProfile
        $env:QT_QPA_PLATFORM = "offscreen"
        $proc = Start-Process -FilePath (Join-Path $releaseDir "SmartClockHost.exe") -PassThru -WindowStyle Hidden
        Start-Sleep -Seconds 8
        if ($proc.HasExited) {
            throw "exe smoke test exited within 8 seconds. ExitCode=$($proc.ExitCode)"
        }
        Stop-Process -Id $proc.Id -Force
    } finally {
        $env:SMARTCLOCK_PROFILE_DIR = $oldProfile
        $env:QT_QPA_PLATFORM = $oldQt
        if (Test-Path $smokeProfile) {
            Remove-Item -LiteralPath $smokeProfile -Recurse -Force
        }
    }
}

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $releaseDir -DestinationPath $zipPath -Force
$forSubmitRelease = Join-Path $repo "for_submit\release"
if (Test-Path $forSubmitRelease) {
    Copy-Item -LiteralPath $zipPath -Destination (Join-Path $forSubmitRelease "SmartClockHost-v2.2.zip") -Force
}

Write-Host "v2.2 release created: $releaseDir"
Write-Host "v2.2 zip created: $zipPath"
Write-Host "User profile preserved at: $profileDir"
