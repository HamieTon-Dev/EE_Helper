# Local Windows build (same steps as the GitHub Actions workflow).
# Requires 64-bit Python 3.12 (`py -3.12`) and, for the COM worker, 32-bit Python 3.12 (`py -3.12-32`).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

py -3.12 -m pip install -r requirements-dev.txt
py -3.12 -m pytest -q
py -3.12 -m PyInstaller --noconfirm packaging/MultisimCopilot.spec

# 32-bit COM worker (Multisim's Automation API is a 32-bit COM server)
py -3.12-32 -m pip install -r requirements-worker.txt pyinstaller
py -3.12-32 -m PyInstaller --noconfirm --onefile --console --name MultisimWorker `
    --paths . --distpath build/worker packaging/worker_entry.py
Copy-Item build/worker/MultisimWorker.exe dist/MultisimCopilot/

# ngspice (simulation engine). Download ngspice-<ver>_64.7z from
# https://ngspice.sourceforge.io/download.html and extract it so that this exists:
#   dist/MultisimCopilot/ngspice/Spice64/bin/ngspice_con.exe
if (-not (Test-Path dist/MultisimCopilot/ngspice/Spice64/bin/ngspice_con.exe)) {
    Write-Warning "ngspice not bundled - extract Spice64 into dist/MultisimCopilot/ngspice, or set its path in Settings."
}
Compress-Archive -Force dist/MultisimCopilot dist/MultisimCopilot-windows.zip
Write-Host "Built dist/MultisimCopilot/MultisimCopilot.exe and dist/MultisimCopilot-windows.zip"
