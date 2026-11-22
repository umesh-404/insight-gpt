# InsightGPT Stop Script
$ErrorActionPreference = 'SilentlyContinue'

Write-Host "Stopping InsightGPT services..." -ForegroundColor Yellow

# Our own two processes only: the API on 8000 and the web app on 3000.
#
# Ollama (11434) is deliberately NOT stopped. It is a shared, machine-wide
# service that other projects on this machine use for their own embeddings and
# models; killing it to "stop InsightGPT" takes those down too, and it is not
# ours to stop. It also costs nothing to leave running — it unloads idle models
# by itself. The same goes for Qdrant: it runs in a container shared with
# another project's collections, so nothing here touches it.
$ports = @(8000, 3000)
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
