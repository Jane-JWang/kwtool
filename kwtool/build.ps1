$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $projectPython -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $projectPython -m PyInstaller --noconfirm --clean --onedir --windowed --name kwtool --collect-all playwright launcher.py
exit $LASTEXITCODE
