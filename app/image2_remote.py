from __future__ import annotations

from pathlib import Path
from typing import Callable
import json
import shlex
import subprocess

from .config import settings


Progress = Callable[[str, int], None]


def _target() -> str:
    return f"{settings.ssh_user}@{settings.ssh_host}"


def _ssh(remote_command: str, *, timeout: int = 2400) -> subprocess.CompletedProcess[str]:
    command = [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=6",
        "-i", str(settings.ssh_key), _target(), remote_command,
    ]
    completed = subprocess.run(command, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "远程命令失败"
        raise RuntimeError(message[-4000:])
    return completed


def _scp(local: Path, remote: str, *, download: bool = False) -> None:
    if download:
        source, destination = f"{_target()}:{remote}", str(local)
    else:
        source, destination = str(local), f"{_target()}:{remote}"
    completed = subprocess.run(
        ["scp", "-q", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
         "-i", str(settings.ssh_key), source, destination],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "SCP 传输失败")


def _remote_file_exists(path: str) -> bool:
    return _ssh(
        f"if test -f {shlex.quote(path)}; then printf yes; fi",
        timeout=45,
    ).stdout.strip() == "yes"


def build_prompt(operation: str, user_prompt: str,
                 inpaint_anything_crop: bool = False) -> str:
    clean = user_prompt.strip()
    if operation == "fill":
        if inpaint_anything_crop:
            return (
                "严格按照 Inpaint Anything 的 Fill Anything 局部窗口执行。"
                "第1张图是从原图围绕选区裁出的唯一 512×512 编辑窗口；"
                "第2张黑白图是原版二值蒙版说明，白色区域是唯一需要填充的洞，"
                "黑色区域必须保持为上下文。"
                f"请在白色区域生成：{clean}。不得缩放、移动或重新构图该编辑窗口，"
                "输出与第1张图完全同尺寸的正方形局部窗口，不添加文字、水印或签名。"
            )
        return (
            "严格局部重绘。第1张图是唯一编辑底图，第2张黑白图是区域说明："
            "白色区域是唯一需要重新生成的区域，黑色区域只提供上下文且不得成为新增内容。"
            f"请在白色区域生成：{clean}。保持原构图、镜头、尺度、透视、光照、材质和边界连续，"
            "输出完整画布，不添加文字、水印或签名。"
        )
    return (
        "保留主体并重新生成背景。第1张图是唯一编辑底图，第2张黑白图是主体说明："
        "白色区域为必须保持身份、姿态、位置、比例和细节的主体，黑色区域为需要重建的背景。"
        f"新背景要求：{clean}。主体与背景接触边缘自然，光影方向一致，输出完整画布，"
        "不添加文字、水印或签名。"
    )


def run_image2(task_id: str, source: Path, mask_guide: Path, width: int, height: int,
               operation: str, user_prompt: str, task_dir: Path,
               progress: Progress,
               inpaint_anything_crop: bool = False) -> tuple[Path, dict]:
    remote_dir = f"{settings.remote_run_root}/{task_id}"
    _ssh(f"install -d -o catsco-agent -g catsco-agent {shlex.quote(remote_dir)}")
    prompt = build_prompt(operation, user_prompt, inpaint_anything_crop)
    raw = f"{operation}: {user_prompt}"
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (task_dir / "raw-request.txt").write_text(raw, encoding="utf-8")
    if _remote_file_exists(f"{remote_dir}/result.json"):
        progress("正在恢复同一 Image2 任务的已有结果", 58)
    else:
        node = settings.remote_node
        skill = settings.remote_skill
        env = (
            "sudo -u catsco-agent -H env HOME=/srv/catsco-agent CATSCO_BOT_UID=407 "
            f"{node}"
        )
        run = shlex.quote(remote_dir)
        if _remote_file_exists(f"{remote_dir}/request.json"):
            command = " && ".join([
                f"cd {settings.remote_root}",
                f"{env} {skill}/scripts/run-image.mjs --provider image2 "
                f"--request {run}/request.json --out-dir {run}",
            ])
            progress("正在恢复同一 Image2 远程任务", 52)
        else:
            progress("上传底图与蒙版说明", 35)
            _scp(source, f"{remote_dir}/source.png")
            _scp(mask_guide, f"{remote_dir}/mask-guide.png")
            _scp(task_dir / "prompt.txt", f"{remote_dir}/prompt.txt")
            _scp(task_dir / "raw-request.txt", f"{remote_dir}/raw-request.txt")
            command = " && ".join([
                f"cd {settings.remote_root}",
                f"{env} {skill}/scripts/prepare-reference.mjs --input {run}/source.png "
                f"--out-dir {run} --role edit_target --use-for "
                f"{shlex.quote('唯一编辑底图；保持整体画布、构图、尺度、视角和未编辑内容')}",
                f"{env} {skill}/scripts/prepare-reference.mjs --input {run}/mask-guide.png "
                f"--out-dir {run} --role reference --use-for "
                f"{shlex.quote('黑白区域说明；白色区域是选择区域，黑色区域是其余画布')}",
                f"{env} {skill}/scripts/prepare-request.mjs --operation edit "
                f"--prompt {run}/prompt.txt --raw-request {run}/raw-request.txt "
                f"--request {run}/request.json --references {run}/references.json "
                f"--aspect-ratio {width}:{height} --target-size {width}x{height} "
                f"--quality high --output-format png",
                f"{env} {skill}/scripts/run-image.mjs --provider image2 "
                f"--request {run}/request.json --out-dir {run}",
            ])
            progress("Image2 正在生成候选图", 52)
        _ssh(command, timeout=2400)

    local_result = task_dir / "image2-result.json"
    _scp(local_result, f"{remote_dir}/result.json", download=True)
    result = json.loads(local_result.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise RuntimeError(json.dumps(result.get("error", result), ensure_ascii=False))
    output = result.get("output", {})
    provider_original = output.get("provider_original")
    if isinstance(provider_original, dict):
        remote_output = provider_original.get("image_path") or provider_original.get("path")
    else:
        remote_output = provider_original
    remote_output = remote_output or output.get("image_path")
    if not remote_output:
        raise RuntimeError("Image2 结果缺少输出文件路径")
    local_output = task_dir / "image2-provider-original.png"
    _scp(local_output, remote_output, download=True)
    progress("候选图已返回，正在锁定蒙版外像素", 82)
    record = {
        "provider": result.get("provider", "image2"),
        "remote_run_dir": remote_dir,
        "remote_output": remote_output,
        "warnings": result.get("warnings", []),
        "request": result.get("request", {}),
    }
    return local_output, record
