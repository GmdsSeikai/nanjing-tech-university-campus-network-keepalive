$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    & .\build.bat
    if ($LASTEXITCODE -ne 0) { throw 'Source tests or Windows build failed.' }
    & .\build\.venv\Scripts\python.exe -u .\scripts\smoke_windows.py
    if ($LASTEXITCODE -ne 0) { throw 'Packaged Windows workflow failed.' }
    & git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'Git diff whitespace check failed.' }
} finally {
    Pop-Location
}
