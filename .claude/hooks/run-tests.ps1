$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $repo 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = 'python'
}

Push-Location $repo
try {
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("The default pytest suite failed. Fix it before stopping.")
        exit 2
    }
}
finally {
    Pop-Location
}
