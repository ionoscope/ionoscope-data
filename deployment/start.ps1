param([switch]$NoBuild)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        docker desktop start --timeout 90
        if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop did not start.' }
    }
    $composeArgs = @('compose', '-f', 'docker-compose.dev.yml', '-p', 'ionoscope-api-check',
                     'up', '-d', '--wait', '--wait-timeout', '180')
    if (-not $NoBuild) { $composeArgs += '--build' }
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) { throw 'IonoScope startup failed. Inspect docker compose logs.' }
    Write-Host 'API: http://localhost:8000/docs'
    Write-Host 'Live cycle: http://localhost:8001/api/v1/live/status'
    Write-Host 'Containers are ready; the first data/forecast cycle may still be running.'
} finally {
    Pop-Location
}
