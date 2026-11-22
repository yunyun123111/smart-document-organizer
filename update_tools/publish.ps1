# 开发机一键发布：构建前端 -> 打增量更新包 -> 推到 GitHub / 局域网分发
param(
    [ValidateSet('github', 'lan', 'both', 'none')]
    [string]$Publish = 'github',
    [switch]$SkipBuild,
    [int]$ServePort = 8765,
    [string]$ReleaseDir = "",
    [string]$Remote = 'origin',
    [string]$Branch = 'releases'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot '_common.ps1')
if (-not $ReleaseDir) { $ReleaseDir = Join-Path $root 'release' }
$stage = Join-Path $env:TEMP 'sdo_release_stage'

function Write-Step($msg) { Write-Host $msg -ForegroundColor Cyan }

function Get-GitExe {
    $p = 'C:\Program Files\Git\cmd\git.exe'
    if (Test-Path $p) { return $p }
    $g = (Get-Command git -ErrorAction SilentlyContinue).Source
    if ($g) { return $g }
    throw "git.exe not found"
}

# git push 走 github.com 常被链路重置；api.github.com 稳定，作为兜底通道
function Publish-ViaApi {
    param([string]$Repo, [string]$ZipPath, [string]$VerPath, [string]$Version)
    $gitExe = Get-GitExe
    $cred = ("protocol=https`nhost=github.com`n`n" | & $gitExe credential fill 2>$null)
    $pw = ($cred | Where-Object { $_ -like 'password=*' }) -replace '^password=', ''
    if (-not $pw) { Write-Host "    no GitHub credential, skip API fallback" -ForegroundColor Yellow; return $false }
    $hdr = @{ Authorization = "Bearer $pw"; Accept = 'application/vnd.github+json'; 'User-Agent' = 'sdo-publish' }
    $allOk = $true
    foreach ($item in @(@('update.zip', $ZipPath), @('version.json', $VerPath))) {
        $name = $item[0]; $full = $item[1]
        $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($full))
        $sha = $null
        try {
            $meta = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/contents/$name`?ref=$Branch" -Headers $hdr -TimeoutSec 60
            $sha = $meta.sha
        } catch { }
        $body = @{ message = "release $Version"; content = $b64; branch = $Branch }
        if ($sha) { $body.sha = $sha }
        try {
            Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/contents/$name" -Method Put -Headers $hdr -Body ($body | ConvertTo-Json -Compress) -ContentType 'application/json' -TimeoutSec 240 | Out-Null
            Write-Host ("    API OK: {0} ({1} bytes)" -f $name, ([IO.File]::ReadAllBytes($full)).Length) -ForegroundColor Green
        } catch {
            Write-Host ("    API FAIL: {0} -> {1}" -f $name, $_.Exception.Message) -ForegroundColor Red
            $allOk = $false
        }
    }
    return $allOk
}

# ---------- 1. 构建前端 ----------
if (-not $SkipBuild) {
    Write-Step "[1/5] Building frontend (npm run build)..."
    Push-Location (Join-Path $root 'frontend')
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "frontend build failed, exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
} else {
    Write-Step "[1/5] Skip frontend build (using existing frontend\dist)"
}

if (-not (Test-Path (Join-Path $root 'frontend\dist\index.html'))) {
    throw "frontend\dist\index.html not found. Build first."
}

# ---------- 2. 组包 ----------
Write-Step "[2/5] Staging files..."
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

function Copy-Tree($src, $dst) {
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Get-ChildItem -LiteralPath $src -Force | ForEach-Object {
        if ($_.PSIsContainer) {
            if ($_.Name -in @('__pycache__', '.pytest_cache', 'node_modules')) { return }
            Copy-Tree $_.FullName (Join-Path $dst $_.Name)
        } else {
            if ($_.Extension -in @('.pyc', '.pyo') -or $_.Name -eq '.tsbuildinfo') { return }
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dst $_.Name) -Force
        }
    }
}

Copy-Tree (Join-Path $root 'backend') (Join-Path $stage 'backend')
Copy-Tree (Join-Path $root 'frontend\dist') (Join-Path $stage 'frontend\dist')
# 更新工具自身也随包下发；但不能覆盖部署机本地配置
Copy-Tree (Join-Path $root 'update_tools') (Join-Path $stage 'update_tools')
Remove-Item (Join-Path $stage 'update_tools\update_config.json') -Force -ErrorAction SilentlyContinue
foreach ($f in @('requirements.txt', 'desktop_app.py', '.env.example')) {
    $p = Join-Path $root $f
    if (Test-Path $p) { Copy-Item -LiteralPath $p -Destination $stage -Force }
}

$reqHash = (Get-FileHash (Join-Path $root 'requirements.txt') -Algorithm SHA256).Hash.ToLower()
$version = Get-Date -Format 'yyyyMMdd.HHmmss'
$git = Get-GitExe
$head = (& $git -C $root rev-parse --short HEAD 2>$null)
$meta = [ordered]@{
    version          = $version
    built_at         = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    requirements_sha = $reqHash
    git_head         = $head
}
$meta | ConvertTo-Json | Set-Content -Path (Join-Path $stage 'version.json') -Encoding UTF8

# ---------- 3. 压缩 ----------
Write-Step "[3/5] Compressing..."
if (-not (Test-Path $ReleaseDir)) { New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null }
$zip = Join-Path $ReleaseDir 'update.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [IO.Compression.CompressionLevel]::Optimal, $false)
$verFile = Join-Path $ReleaseDir 'version.json'
Copy-Item (Join-Path $stage 'version.json') $verFile -Force

# 外部 version.json 追加校验信息：部署机据此拒绝缓存错配的旧包
$zipHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
$zipSize = (Get-Item -LiteralPath $zip).Length
$metaOut = [ordered]@{
    version          = $version
    built_at         = $meta.built_at
    requirements_sha = $reqHash
    git_head         = $head
    zip_sha256       = $zipHash
    zip_size         = $zipSize
}
[IO.File]::WriteAllText($verFile, ($metaOut | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))

Write-Host ("    version   : {0}" -f $version)
Write-Host ("    update.zip: {0} MB" -f ([math]::Round((Get-Item $zip).Length / 1MB, 2)))

# ---------- 4. 推 GitHub ----------
if ($Publish -eq 'github' -or $Publish -eq 'both') {
    Write-Step "[4/5] Publishing to GitHub branch '$Branch'..."
    $tmp = Join-Path $env:TEMP 'sdo_rel_push'
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null

    & $git -C $tmp init -q -b $Branch
    Copy-Item $zip (Join-Path $tmp 'update.zip') -Force
    Copy-Item $verFile (Join-Path $tmp 'version.json') -Force
    & $git -C $tmp add -A
    & $git -C $tmp -c user.name=SmartDoc -c user.email=smartdoc@local commit -q -m "release $version"

    $url = (& $git -C $root remote get-url $Remote).Trim()
    $repoPath = $url -replace '^https://github\.com/', '' -replace '\.git$', ''
    if ($repoPath -notmatch '/') {
        $repoPath = (& $git -C $root config --get remote.$Remote.url) -replace '.*github\.com[:/]', '' -replace '\.git$', ''
    }

    $pushed = $false
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    for ($i = 1; $i -le 3; $i++) {
        Write-Host "    git push attempt $i ..."
        & $git -c http.version=HTTP/1.1 -C $tmp push -f $url "HEAD:refs/heads/$Branch" 2>&1 | ForEach-Object { Write-Host "      $_" }
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        if ($i -lt 3) { Start-Sleep -Seconds 5 }
    }
    $ErrorActionPreference = $oldEap

    if (-not $pushed) {
        Write-Host "    git push blocked by network, falling back to GitHub API..." -ForegroundColor Yellow
        $pushed = Publish-ViaApi -Repo $repoPath -ZipPath $zip -VerPath $verFile -Version $version
    }
    if (-not $pushed) { throw "GitHub publish failed (both git push and API fallback)." }
    Write-Host ("    OK -> https://raw.githubusercontent.com/{0}/{1}/update.zip" -f $repoPath, $Branch) -ForegroundColor Green
}

# ---------- 5. 局域网分发 ----------
if ($Publish -eq 'lan' -or $Publish -eq 'both') {
    Write-Step "[5/5] Starting LAN distribution on port $ServePort..."
    $conns = Get-NetTCPConnection -LocalPort $ServePort -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }

    $py = Resolve-Python
    if (-not $py) { throw "python.exe not found on this machine" }
    Start-Process -FilePath $py `
        -ArgumentList "-m http.server $ServePort --directory `"$ReleaseDir`"" `
        -WindowStyle Hidden
    Start-Sleep -Seconds 2

    $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '0.0.0.0' -and $_.PrefixOrigin -ne 'WellKnown' } |
           Select-Object -First 1).IPAddress
    if (-not $ip) { $ip = '127.0.0.1' }
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$ServePort/version.json" -UseBasicParsing -TimeoutSec 5 | Out-Null
        Write-Host ("    OK, serving at http://{0}:{1}/update.zip" -f $ip, $ServePort) -ForegroundColor Green
        Write-Host "    Keep this window open while the deployment PC syncs."
    } catch {
        throw "distribution service did not respond on port $ServePort"
    }
} elseif ($Publish -eq 'github') {
    Write-Step "[5/5] No LAN service needed (GitHub-only publish). You can close this window."
}

Write-Host ""
Write-Host "Done. Deployment PC will pick it up on its next check."
