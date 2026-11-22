# 部署机：检查并应用更新。可由计划任务每 10 分钟调用一次。
param(
    [string]$ConfigPath = "",
    [switch]$Force,          # 忽略版本比对，强制重新部署
    [switch]$NoRestart,      # 只覆盖文件，不重启服务
    [int]$TimeoutSec = 15
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot '_common.ps1')
if (-not $ConfigPath) { $ConfigPath = Join-Path $PSScriptRoot 'update_config.json' }

$logDir = Join-Path $root 'data'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$logFile = Join-Path $logDir 'update.log'

function Write-Log($msg, $level = 'INFO') {
    $line = "[{0}] [{1}] {2}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $level, $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

function Get-ListenPid($port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.OwningProcess }
    return $null
}

function Wait-Health($port, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/system/health" -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 800
    }
    return $false
}

# ---------- 读配置 ----------
if (-not (Test-Path $ConfigPath)) {
    Write-Log "config not found: $ConfigPath" 'ERROR'
    exit 1
}
$cfg = Get-Content -Path $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$port = [int]$cfg.port
if (-not $port) { $port = 8000 }

$provider = $cfg.provider
if (-not $provider) {
    if ($cfg.repo) { $provider = 'github' } else { $provider = 'http' }
}

# 候选源：GitHub 直连/镜像（不受开发机是否开机影响），局域网地址作为备用
$raw = ''
$mirrorBase = ''
if ($cfg.repo) {
    $branch = $cfg.branch
    if (-not $branch) { $branch = 'releases' }
    $raw = "https://raw.githubusercontent.com/$($cfg.repo)/$branch"
    if ($cfg.mirror) { $mirrorBase = "$($cfg.mirror.TrimEnd('/'))/$raw" }
}
# version.json 只有几百字节，直连优先：镜像 CDN 会缓存旧版本，导致误判"已是最新"
$verBases = @()
if ($raw) { $verBases += $raw; if ($mirrorBase) { $verBases += $mirrorBase } }
# update.zip 体积大，镜像优先
$zipBases = @()
if ($raw) { if ($mirrorBase) { $zipBases += $mirrorBase }; $zipBases += $raw }
if ($cfg.source) { $verBases += $cfg.source.TrimEnd('/'); $zipBases += $cfg.source.TrimEnd('/') }

if ($verBases.Count -eq 0) {
    Write-Log "no update source configured (repo/source both empty)" 'ERROR'
    exit 1
}

# ---------- 取远端版本 ----------
$remote = $null
foreach ($b in $verBases) {
    try {
        $remote = Invoke-RestMethod -Uri "$b/version.json" -UseBasicParsing -TimeoutSec $TimeoutSec
        Write-Log "version source OK: $b ($($remote.version))"
        break
    } catch {
        Write-Log "source failed ($b): $($_.Exception.Message)" 'WARN'
    }
}
if (-not $remote) {
    Write-Log "all sources unreachable, skip this round" 'WARN'
    exit 0
}

$localFile = Join-Path $root 'data\deployed_version.txt'
$localVersion = ''
if (Test-Path $localFile) { $localVersion = (Get-Content $localFile -Raw).Trim() }

# 版本号格式 yyyyMMdd.HHmm，取最大者作为"已部署"
$known = @()
if ($localVersion) { $known = @($localVersion) }

if (-not $Force -and $localVersion -and $remote.version.CompareTo($localVersion) -le 0) {
    Write-Log "up to date ($localVersion)"
    exit 0
}

Write-Log "new version: $($remote.version) (local: $($localVersion -replace '^$','none'))"

# ---------- 下载 + 校验（逐个源尝试，拒绝缓存错配的旧包） ----------
$tmpDir = Join-Path $root 'data\temp\update'
if (-not (Test-Path $tmpDir)) { New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null }
$zip = Join-Path $tmpDir 'update.zip'
Add-Type -AssemblyName System.IO.Compression.FileSystem

$gotOk = $false
$base = $null
foreach ($b in $zipBases) {
    try {
        if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
        Invoke-WebRequest -Uri "$b/update.zip" -OutFile $zip -UseBasicParsing -TimeoutSec 180
    } catch {
        Write-Log "download failed ($b): $($_.Exception.Message)" 'WARN'
        continue
    }
    $len = (Get-Item -LiteralPath $zip).Length
    if ($remote.zip_size -and ([int64]$remote.zip_size -ne $len)) {
        Write-Log "size mismatch ($b): got $len, want $($remote.zip_size)" 'WARN'
        continue
    }
    if ($remote.zip_sha256) {
        $h = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
        if ($h -ne ([string]$remote.zip_sha256).ToLower()) {
            Write-Log "sha256 mismatch ($b), package is stale" 'WARN'
            continue
        }
    }
    $innerOk = $false
    try {
        $z = [IO.Compression.ZipFile]::OpenRead($zip)
        $names = $z.Entries | ForEach-Object { $_.FullName.Replace('\', '/') }
        $vEntry = $z.Entries | Where-Object { $_.FullName.Replace('\', '/') -eq 'version.json' } | Select-Object -First 1
        $inner = $null
        if ($vEntry) {
            $sr = New-Object IO.StreamReader -ArgumentList $vEntry.Open()
            $inner = $sr.ReadToEnd() | ConvertFrom-Json
            $sr.Close()
        }
        $z.Dispose()
        if (($names -contains 'backend/main.py') -and ($names -contains 'frontend/dist/index.html') -and $inner -and ([string]$inner.version -eq [string]$remote.version)) {
            $innerOk = $true
        }
        if (-not $innerOk) { Write-Log "content check failed ($b): entries or inner version not matching $($remote.version)" 'WARN' }
    } catch {
        Write-Log "zip corrupted ($b): $($_.Exception.Message)" 'WARN'
    }
    if ($innerOk) { $gotOk = $true; $base = $b; break }
}
if (-not $gotOk) {
    Write-Log "no usable package this round (all sources stale or unreachable), will retry later" 'WARN'
    exit 0
}

$sizeMB = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 2)
Write-Log "package verified from $base ($sizeMB MB)"

# ---------- 停服务 ----------
$pid8000 = Get-ListenPid $port
if ($pid8000) {
    Write-Log "stopping service (pid $pid8000, port $port)"
    Stop-Process -Id $pid8000 -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    if (Get-ListenPid $port) { Write-Log "port $port still busy, abort" 'ERROR'; exit 1 }
} else {
    Write-Log "service not running on port $port"
}

# ---------- 备份 ----------
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$bkRoot = Join-Path $root 'data\backup'
$bk = Join-Path $bkRoot ("app_{0}" -f $stamp)
New-Item -ItemType Directory -Force -Path $bk | Out-Null
foreach ($d in @('backend', 'frontend\dist')) {
    $src = Join-Path $root $d
    if (Test-Path $src) {
        $dst = Join-Path $bk $d
        New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
        Write-Log "backup: $d"
    }
}
foreach ($f in @('requirements.txt', 'desktop_app.py')) {
    $src = Join-Path $root $f
    if (Test-Path $src) {
        $dst = Join-Path $bk $f
        New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
}
# 只保留最近 5 份备份
Get-ChildItem $bkRoot -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -Skip 5 |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

# ---------- 覆盖 ----------
$extract = Join-Path $tmpDir 'extract'
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
New-Item -ItemType Directory -Force -Path $extract | Out-Null
[IO.Compression.ZipFile]::ExtractToDirectory($zip, $extract)

try {
    Copy-Item -Path (Join-Path $extract '*') -Destination $root -Recurse -Force
    Write-Log "files replaced"
} catch {
    Write-Log "copy failed: $($_.Exception.Message), rolling back" 'ERROR'
    Copy-Item -Path (Join-Path $bk '*') -Destination $root -Recurse -Force
    exit 1
}

# ---------- 依赖 ----------
$reqFile = Join-Path $root 'requirements.txt'
$reqHashNew = ''
if (Test-Path $reqFile) { $reqHashNew = (Get-FileHash $reqFile -Algorithm SHA256).Hash.ToLower() }
$reqMarker = Join-Path $root 'data\requirements_sha.txt'
$reqHashOld = ''
if (Test-Path $reqMarker) { $reqHashOld = (Get-Content $reqMarker -Raw).Trim() }
if ($reqHashNew -and $reqHashNew -ne $reqHashOld) {
    Write-Log "requirements changed, installing dependencies (may take minutes)"
    $py = Resolve-Python -Preferred $cfg.python
    if (-not $py) {
        Write-Log "python.exe not found, cannot install dependencies" 'ERROR'
        Copy-Item -Path (Join-Path $bk '*') -Destination $root -Recurse -Force
        exit 1
    }
    $ver = (& $py -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null)
    Write-Log "using python $ver ($py)"
    if ($ver -notmatch '3\.(11|12)') {
        Write-Log "warning: project targets Python 3.11/3.12, got $ver. pip may fail." 'WARN'
    }
    & $py -m pip install -r $reqFile --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) {
        Write-Log "pip install failed (exit $LASTEXITCODE), rolling back" 'ERROR'
        Copy-Item -Path (Join-Path $bk '*') -Destination $root -Recurse -Force
        if (-not $NoRestart) { & (Join-Path $PSScriptRoot 'start-service.ps1') -Port $port -Python $py | Out-Null }
        exit 1
    }
    Set-Content -Path $reqMarker -Value $reqHashNew -Encoding ASCII
}

# ---------- 重启 ----------
if (-not $NoRestart) {
    Write-Log "restarting service"
    & (Join-Path $PSScriptRoot 'start-service.ps1') -Port $port | Out-Null
    if (-not (Wait-Health $port 40)) {
        Write-Log "health check failed after restart, rolling back" 'ERROR'
        $p = Get-ListenPid $port
        if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
        Copy-Item -Path (Join-Path $bk '*') -Destination $root -Recurse -Force
        & (Join-Path $PSScriptRoot 'start-service.ps1') -Port $port | Out-Null
        exit 1
    }
    Write-Log "health check OK"
}

Set-Content -Path $localFile -Value $remote.version -Encoding ASCII
Write-Log "deployed $($remote.version)"
exit 0
