param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$entryPoint = Join-Path $repoRoot "scripts\ck3_gui.py"
$versionFile = Join-Path $repoRoot "packaging\version_info.txt"
$iconFile = Join-Path $repoRoot "packaging\app.ico"
$distPath = Join-Path $repoRoot "dist"
$workPath = Join-Path $repoRoot "build\pyinstaller"
$specPath = Join-Path $repoRoot "build"

& $Python (Join-Path $repoRoot "packaging\create_icon.py") --output $iconFile
if ($LASTEXITCODE -ne 0) {
    throw "Icon generation failed with exit code $LASTEXITCODE"
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "CK3_Mod_Translator" `
    --icon $iconFile `
    --version-file $versionFile `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    $entryPoint

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $distPath "CK3_Mod_Translator.exe"
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $exe
Write-Host "Built: $exe"
Write-Host "SHA256: $($hash.Hash)"

& $Python (Join-Path $repoRoot "packaging\create_release.py") `
    --exe $exe `
    --guide (Join-Path $repoRoot "packaging\Quick_Start.txt") `
    --guide-ja (Join-Path $repoRoot "packaging\使い方.txt") `
    --version "2.0.0"

if ($LASTEXITCODE -ne 0) {
    throw "Release packaging failed with exit code $LASTEXITCODE"
}
