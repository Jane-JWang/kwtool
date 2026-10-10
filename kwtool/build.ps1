$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$repo = $env:KWTOOL_UPDATE_REPO
if (-not $repo) { $repo = 'Jane-JWang/kwtool' }
if ($repo -and $repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'KWTOOL_UPDATE_REPO must be owner/repo' }
Set-Content -LiteralPath (Join-Path $PSScriptRoot 'update_repo.txt') -Value $repo -Encoding utf8

$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $projectPython -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $projectPython -m PyInstaller --noconfirm --clean --onedir --windowed --name kwtool --collect-all playwright --add-data "update_repo.txt;." launcher.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$archive = Join-Path $PSScriptRoot 'dist\kwtool-windows.zip'
if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive }
Compress-Archive -Path (Join-Path $PSScriptRoot 'dist\kwtool') -DestinationPath $archive
exit $LASTEXITCODE
