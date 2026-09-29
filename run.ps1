# InsightGPT One-Click Launcher
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'SilentlyContinue'

$RepoDir = "d:\capstone\insight-gpt"
$ApiDir = Join-Path $RepoDir "services\api"
$WebDir = Join-Path $RepoDir "web"
$PythonExe = Join-Path $ApiDir ".venv\Scripts\python.exe"
$OllamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"

function Test-IsUp($url) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "       Starting InsightGPT Platform       " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Ensure Desktop Shortcut exists
$DesktopDir = [Environment]::GetFolderPath("Desktop")
if ($DesktopDir -and (Test-Path $DesktopDir)) {
    $ShortcutPath = Join-Path $DesktopDir "InsightGPT.lnk"
    if (-not (Test-Path $ShortcutPath)) {
        try {
            $WScriptShell = New-Object -ComObject WScript.Shell
            $Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
            $Shortcut.TargetPath = Join-Path $RepoDir "run.bat"
            $Shortcut.WorkingDirectory = $RepoDir
            $Shortcut.Description = "Launch InsightGPT Application"
            $Shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
            $Shortcut.Save()
            Write-Host "[+] Desktop shortcut created: $ShortcutPath" -ForegroundColor Green
        } catch {}
    }
}

# 2. Check and start Ollama
Write-Host "`n[1/3] Checking LLM Service (Ollama)..." -ForegroundColor Yellow
$ollamaUp = Test-IsUp "http://127.0.0.1:11434"
if (-not $ollamaUp) {
    Write-Host "  -> Starting Ollama server in background..." -ForegroundColor Gray
    if (Test-Path $OllamaExe) {
        Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Minimized
    } else {
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Minimized
    }
    
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 1
        if (Test-IsUp "http://127.0.0.1:11434") { $ollamaUp = $true; break }
    }
}
if ($ollamaUp) {
    Write-Host "  [OK] Ollama is active on http://127.0.0.1:11434" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Ollama is initializing..." -ForegroundColor Gray
}

# 3. Check and start API Backend
Write-Host "`n[2/3] Checking Backend API..." -ForegroundColor Yellow
$backendUp = Test-IsUp "http://localhost:8000/health"
if (-not $backendUp) {
    Write-Host "  -> Starting FastAPI backend (port 8000)..." -ForegroundColor Gray
    $cmd = "cd '$ApiDir'; `$env:LLM_PROVIDER='ollama'; & '$PythonExe' -m uvicorn app.api.main:app --port 8000"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-WindowStyle", "Minimized", "-Command", $cmd
    
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        if (Test-IsUp "http://localhost:8000/health") { $backendUp = $true; break }
    }
}
if ($backendUp) {
    Write-Host "  [OK] Backend API is active on http://localhost:8000" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Backend API is starting up..." -ForegroundColor Gray
}

# 4. Check and start Web Frontend
Write-Host "`n[3/3] Checking Web Frontend..." -ForegroundColor Yellow
$webUp = Test-IsUp "http://localhost:3000"
if (-not $webUp) {
    Write-Host "  -> Starting Next.js frontend (port 3000)..." -ForegroundColor Gray
    $cmd = "cd '$WebDir'; npx next dev -p 3000"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-WindowStyle", "Minimized", "-Command", $cmd
    
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        if (Test-IsUp "http://localhost:3000") { $webUp = $true; break }
    }
}
if ($webUp) {
    Write-Host "  [OK] Web Frontend is active on http://localhost:3000" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Frontend is compiling..." -ForegroundColor Gray
}

# 5. Open browser
Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host " Opening InsightGPT in your browser...   " -ForegroundColor Green
Write-Host " URL:         http://localhost:3000      " -ForegroundColor White
Write-Host " Credentials: admin@insightgpt.dev / admin-pass" -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan

Start-Process "http://localhost:3000"
Start-Sleep -Seconds 2
