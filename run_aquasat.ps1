$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$requiredFiles = @('app.py', 'dashboard_data.csv', 'reservoir_forecasts.csv', 'requirements.txt')
$missingFiles = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $projectRoot $_) -PathType Leaf) })
if ($missingFiles.Count -gt 0) {
    Write-Host "ERROR: Missing required AquaSat files: $($missingFiles -join ', '). Restore them to $projectRoot before launching."
    exit 1
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host 'ERROR: Python 3.10 or newer is required. Install Python, then reopen PowerShell.'
    exit 1
}

$pythonVersion = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'ERROR: Could not determine the Python version.'
    exit $LASTEXITCODE
}
$versionParts = $pythonVersion.Trim().Split('.')
$pythonMajor = [int]$versionParts[0]
$pythonMinor = [int]$versionParts[1]
if (($pythonMajor -lt 3) -or (($pythonMajor -eq 3) -and ($pythonMinor -lt 10))) {
    Write-Host "ERROR: Python 3.10 or newer is required; found $($pythonVersion.Trim())."
    exit 1
}

& python -c "import importlib.util, sys; required = ['pandas', 'plotly', 'streamlit', 'fastapi', 'uvicorn', 'requests']; missing = [name for name in required if importlib.util.find_spec(name) is None]; print('Missing app packages: ' + ', '.join(missing) if missing else 'App packages are installed.'); sys.exit(bool(missing))"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Install the app dependencies with: python -m pip install -r requirements.txt'
    exit 1
}

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'metrics.csv') -PathType Leaf)) {
    Write-Host 'metrics.csv is missing; the app can still start, but calibration metrics will show as unavailable.'
}

Write-Host 'Starting the local AquaSat REST API...'
$apiProcess = $null
$streamlitExitCode = 1
try {
    $apiProcess = Start-Process -FilePath 'python' -ArgumentList @(
        '-m', 'uvicorn', 'aquasat_api:app', '--host', '127.0.0.1', '--port', '8765'
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $projectRoot 'aquasat_api.stdout.log') `
        -RedirectStandardError (Join-Path $projectRoot 'aquasat_api.stderr.log')

    $apiReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($apiProcess.HasExited) {
            break
        }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 2
            if ($health.service -eq 'aquasat-api') {
                $apiReady = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }

    if (-not $apiReady) {
        Write-Host 'ERROR: AquaSat API did not start. Check aquasat_api.stderr.log.'
        $streamlitExitCode = 1
    } else {
        Write-Host 'AquaSat API ready at http://127.0.0.1:8765/docs'
        Write-Host 'Starting Streamlit. Press Ctrl+C in this window to stop AquaSat.'
        & python -m streamlit run (Join-Path $projectRoot 'app.py')
        $streamlitExitCode = $LASTEXITCODE
    }
} finally {
    if ($apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -Force
    }
}
exit $streamlitExitCode
