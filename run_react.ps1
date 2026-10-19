$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$frontendRoot = Join-Path $projectRoot 'frontend'
Set-Location -LiteralPath $projectRoot

foreach ($required in @('python', 'npm')) {
    if (-not (Get-Command $required -ErrorAction SilentlyContinue)) {
        throw "Required command '$required' was not found. Install Python 3.10+ and Node.js 20.19+ (or 22.12+), then reopen PowerShell."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'package.json'))) {
    throw "React frontend files were not found at $frontendRoot."
}

$apiProcess = $null
try {
    Write-Host 'Starting AquaSat API at http://127.0.0.1:8765 ...'
    $apiProcess = Start-Process -FilePath 'python' -ArgumentList @(
        '-m', 'uvicorn', 'aquasat_api:app', '--host', '127.0.0.1', '--port', '8765'
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $projectRoot 'aquasat_api.stdout.log') `
        -RedirectStandardError (Join-Path $projectRoot 'aquasat_api.stderr.log')

    $apiReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($apiProcess.HasExited) { break }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 2
            if ($health.service -eq 'aquasat-api') { $apiReady = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $apiReady) {
        throw "AquaSat API did not become ready. See $projectRoot\aquasat_api.stderr.log"
    }

    Write-Host 'Starting React at http://127.0.0.1:5173 ...'
    Set-Location -LiteralPath $frontendRoot
    if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules'))) {
        Write-Host 'Installing frontend packages for the first run...'
        & npm install
        if ($LASTEXITCODE -ne 0) { throw 'npm install failed.' }
    }
    & npm run dev
    if ($LASTEXITCODE -ne 0) { throw "React dev server exited with code $LASTEXITCODE." }
}
finally {
    if ($apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -Force
    }
    Set-Location -LiteralPath $projectRoot
}
