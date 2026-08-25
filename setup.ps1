# InsightGPT — one-command setup for Windows.
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Doctor
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Repair
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Native
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -SkipModels
#
# This wrapper only finds a usable Python. All of the real work — and every
# platform difference — lives in scripts/setup.py, so the Windows and Unix
# entry points can never drift apart.

[CmdletBinding()]
param(
    [switch]$Doctor,
    [switch]$Repair,
    [switch]$Native,
    [switch]$SkipModels,
    [switch]$NoVerify
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

# Map the PowerShell switches onto the script's flags.
$scriptArgs = @()
if ($Doctor)     { $scriptArgs += '--doctor' }
if ($Repair)     { $scriptArgs += '--repair' }
if ($Native)     { $scriptArgs += '--native' }
if ($SkipModels) { $scriptArgs += '--skip-models' }
if ($NoVerify)   { $scriptArgs += '--no-verify' }

function Find-Python {
    foreach ($candidate in @('python', 'python3', 'py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -eq $cmd) { continue }
        # `python` on a clean Windows box is often the Store alias stub, which
        # exits non-zero and opens the Store — the version probe filters it out.
        & $cmd.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $cmd.Source }
    }
    return $null
}

$python = Find-Python

if ($null -eq $python) {
    Write-Host 'Python 3.9+ was not found. Installing uv to provide one...'
    if ($null -eq (Get-Command uv -ErrorAction SilentlyContinue)) {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
    }
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $uv) {
        Write-Error @'
Could not find or install a Python 3.9+ interpreter.

Install Python from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),
or uv from https://docs.astral.sh/uv/getting-started/installation/, then re-run setup.ps1.
'@
        exit 1
    }
    & $uv.Source run --python 3.12 --no-project scripts/setup.py @scriptArgs
    exit $LASTEXITCODE
}

& $python scripts/setup.py @scriptArgs
exit $LASTEXITCODE
