# InsightGPT Stop Script
$ErrorActionPreference = 'SilentlyContinue'

Write-Host "Stopping InsightGPT services..." -ForegroundColor Yellow

# Stop ports 8000, 3000, 11434
$ports = @(8000, 3000, 11434)
foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($conns) {
        $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($p in $pids) {
            if ($p -and $p -ne 0) {
                Write-Host "Stopping process PID $p on port $port..." -ForegroundColor Gray
                Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

Write-Host "InsightGPT services stopped." -ForegroundColor Green
Start-Sleep -Seconds 2
