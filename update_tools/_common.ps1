# 公共函数：定位项目应使用的 Python 解释器
function Resolve-Python {
    param([string]$Preferred = "")

    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    $candidates += 'C:\Python312\python.exe'
    $candidates += 'C:\Program Files\Python312\python.exe'
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')

    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    $g = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($g) { return $g }
    return $null
}
