$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force 'work/tmp' | Out-Null
$env:TEMP = Join-Path $PSScriptRoot 'work/tmp'
$env:TMP = $env:TEMP
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败。请使用 Python 3.12 64 位。' }
}
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。真实发送依赖建议使用 Python 3.12 64 位。预览页面仅需 Python 标准库。' }
Write-Host '安装完成，双击 start.bat 启动。'
