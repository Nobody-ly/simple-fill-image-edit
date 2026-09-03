$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = "D:\codex_workspace\catsco-inpaint-anything-local\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到现有隔离 Python 环境：$python"
}
$env:CATSCO_INPAINT_PORT = "7864"
$env:CATSCO_INPAINT_DATA = Join-Path $projectRoot "data"
$env:WAVESPEED_API_KEY_FILE = "D:\codex_workspace\catsco-inpaint-object-edit-v2\data\.secrets\wavespeed-api-key.txt"
Set-Location -LiteralPath $projectRoot
& $python run.py
