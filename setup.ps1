$ScriptDir = $PSScriptRoot

if (-not (Test-Path "venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv venv
}

Write-Host "Installing packages..."
& ".\venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host "Activating virtual environment..."
& ".\venv\Scripts\Activate.ps1"

Write-Host "Setup complete. Run .\run.ps1 to start."