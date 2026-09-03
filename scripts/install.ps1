$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venv = Join-Path $projectRoot ".venv"
if (-not (Test-Path -LiteralPath $venv)) {
    python -m venv $venv
}
$python = Join-Path $venv "Scripts\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu126
& $python -m pip install -r (Join-Path $projectRoot "requirements.txt")
& $python -c "import torch; assert torch.cuda.is_available(), 'CUDA PyTorch 未检测到 GPU'; print(torch.__version__, torch.cuda.get_device_name(0))"

