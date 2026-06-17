$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "未找到虚拟环境解释器: $python" -ForegroundColor Red
    Write-Host "请先按 README 或 docs/deployment.md 创建 .venv" -ForegroundColor Yellow
    exit 1
}

Push-Location $root
try {
    & $python ".\app.py"
}
finally {
    Pop-Location
}
