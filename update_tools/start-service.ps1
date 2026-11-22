# 部署机：后台启动服务（无窗口），供开机自启与更新后重启调用
param(
    [int]$Port = 8000,
    [switch]$Force,
    [string]$Python = ""
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot '_common.ps1')

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing -and -not $Force) {
    Write-Host "[INFO] service already listening on port $Port (pid $($existing.OwningProcess))"
    exit 0
}
if ($existing -and $Force) {
    Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

$py = Resolve-Python -Preferred $Python
if (-not $py -or -not (Test-Path $py)) {
    Write-Host "[ERROR] python.exe not found. Install Python 3.12 with PATH enabled."
    exit 1
}

$logDir = Join-Path $root 'data'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

Start-Process -FilePath $py `
    -ArgumentList "-m uvicorn backend.main:app --host 0.0.0.0 --port $Port" `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'server_out.log') `
    -RedirectStandardError  (Join-Path $logDir 'server_err.log')

$deadline = (Get-Date).AddSeconds(40)
$ok = $false
while ((Get-Date) -lt $deadline) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { $ok = $true; break }
    Start-Sleep -Milliseconds 800
}

if ($ok) {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
           Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
           Select-Object -First 1).IPAddress
    Write-Host "[OK] service started: http://127.0.0.1:$Port  (LAN: http://${ip}:$Port)"
    exit 0
} else {
    Write-Host "[ERROR] service did not start in 40s, check data\server_err.log"
    exit 1
}
