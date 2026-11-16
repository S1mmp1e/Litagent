$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python is missing. Create .venv and install requirements.txt first.'
}
& $python -m scripts.launch
if ($LASTEXITCODE -ne 0) { throw 'LitAgent did not start. See the diagnostic message above.' }
