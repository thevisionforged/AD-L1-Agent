# AD Identity L1 Virtual Agent + AD Ops Console - one-command start for Windows.
# Built by Samarth Pandey (hack-grp-22). HCLTech DFS TechBee AI Hackathon, Use Case 05.
#
# Usage (from the project root):  powershell -ExecutionPolicy Bypass -File .\run.ps1
# Creates .venv if missing, installs pinned dependencies, creates .env from .env.example if missing,
# then starts the web app on http://127.0.0.1:8000 (end-user agent) and /ops (AD Ops Console).
# No interactive prompts.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Find-Python {
    $candidates = @(
        @{ exe = "py"; args = @("-3.13") },
        @{ exe = "py"; args = @("-3.12") },
        @{ exe = "py"; args = @("-3") },
        @{ exe = "python"; args = @() }
    )
    foreach ($c in $candidates) {
        if (Get-Command $c.exe -ErrorAction SilentlyContinue) {
            try {
                $v = & $c.exe @($c.args + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")) 2>$null
                if ($LASTEXITCODE -eq 0 -and $v) {
                    $parts = "$v".Trim().Split(".")
                    if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) { return $c }
                }
            } catch { }
        }
    }
    throw "Python 3.11+ was not found. Install Python 3.13 and re-run this script."
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $py = Find-Python
    Write-Host "Creating virtual environment (.venv) ..."
    & $py.exe @($py.args + @("-m", "venv", ".venv"))
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtual environment." }
}

Write-Host "Installing dependencies from requirements.txt ..."
& $venvPython -m pip install --disable-pip-version-check --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed. Check network access to PyPI." }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Put your key in OPENAI_API_KEY for online mode;"
    Write-Host "without a key the app runs in deterministic offline mode."
}

$port = 8000
$envLine = Select-String -Path ".env" -Pattern '^\s*APP_PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($envLine) { $port = [int]$envLine.Matches[0].Groups[1].Value }

Write-Host ""
Write-Host "End-user L1 Virtual Agent : http://127.0.0.1:$port/"
Write-Host "AD Ops Console (engineers): http://127.0.0.1:$port/ops"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

# Open the browser a few seconds after the server starts (non-blocking).
Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 5; Start-Process $u } -ArgumentList "http://127.0.0.1:$port/" | Out-Null

& $venvPython "src\serve.py"
