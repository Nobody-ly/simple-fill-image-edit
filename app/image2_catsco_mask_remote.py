from __future__ import annotations

from pathlib import Path
from typing import Callable
import json
import shlex

import numpy as np
from PIL import Image

from .config import settings
from .image2_openai import build_alpha_mask
from .image2_remote import _remote_file_exists, _scp, _ssh


Progress = Callable[[str, int], None]


def run_catsco_masked_image2(
    task_id: str,
    source: Path,
    mask: np.ndarray,
    prompt: str,
    task_dir: Path,
    progress: Progress,
) -> tuple[Path, dict]:
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
    if mask.shape != (image.height, image.width):
        raise RuntimeError("CatsCo 原生 mask 路由的底图与蒙版尺寸不一致")
    ia_size = image.size
    provider_size = ia_size
    if image.width * image.height < 655_360:
        provider_size = (1024, 1024)
        image = image.resize(provider_size, Image.Resampling.LANCZOS)
        mask = np.asarray(
            Image.fromarray(mask).resize(provider_size, Image.Resampling.NEAREST),
            dtype=np.uint8,
        )
    source_path = task_dir / "image2-native-source.png"
    mask_path = task_dir / "image2-native-alpha-mask.png"
    prompt_path = task_dir / "image2-native-prompt.txt"
    image.save(source_path, format="PNG")
    build_alpha_mask(mask).save(mask_path, format="PNG")
    prompt_path.write_text(
        "只修改透明蒙版指定的局部区域，保留其余构图、人物、姿态、透视、光照和视觉关系。"
        "让修改内容在边界处与原图自然衔接。目标内容：" + prompt.strip(),
        encoding="utf-8",
    )

    remote_dir = f"{settings.remote_run_root}/{task_id}-native-mask"
    result_remote = f"{remote_dir}/native-mask-result.png"
    metadata_remote = f"{remote_dir}/native-mask-response.json"
    _ssh(f"install -d -o catsco-agent -g catsco-agent {shlex.quote(remote_dir)}")
    if not (_remote_file_exists(result_remote) and _remote_file_exists(metadata_remote)):
        progress("上传 IA 局部窗口与原生透明蒙版", 36)
        _scp(source_path, f"{remote_dir}/source.png")
        _scp(mask_path, f"{remote_dir}/mask.png")
        _scp(prompt_path, f"{remote_dir}/prompt.txt")
        _scp(settings.root / "scripts" / "catsco-masked-image2.mjs", f"{remote_dir}/runner.mjs")
        command = (
            "runuser -u catsco-agent -- bash -lc "
            + shlex.quote(
                "set -a; . /srv/catsco-agent/.env; set +a; "
                f"exec {settings.remote_node} {remote_dir}/runner.mjs "
                f"--source {remote_dir}/source.png --mask {remote_dir}/mask.png "
                f"--prompt {remote_dir}/prompt.txt --out-dir {remote_dir} "
                f"--model gpt-image-2 --size {provider_size[0]}x{provider_size[1]} "
                "--quality high --timeout 600000"
            )
        )
        progress("CatsCo 网关正在调用 Image2 原生 mask 线路", 48)
        try:
            _ssh(command, timeout=900)
        except Exception:
            # The paid upstream request may have completed even if the SSH
            # transport disconnected while returning stdout. Resume the same
            # task from its durable remote artifacts instead of submitting a
            # second paid generation.
            if not (
                _remote_file_exists(result_remote)
                and _remote_file_exists(metadata_remote)
            ):
                raise
            progress("连接已恢复，正在读取同一任务的持久化结果", 58)
    else:
        progress("正在恢复同一 CatsCo 原生 mask 结果", 58)

    local_output = task_dir / "image2-native-mask-provider-original.png"
    local_metadata = task_dir / "image2-native-mask-response.json"
    _scp(local_output, result_remote, download=True)
    _scp(local_metadata, metadata_remote, download=True)
    record = json.loads(local_metadata.read_text(encoding="utf-8"))
    progress("CatsCo 原生蒙版结果已返回", 82)
    return local_output, {
        "provider": "catsco-gpt-image-2-native-mask",
        "provider_lane": record.get("provider"),
        "request_id": record.get("request_id"),
        "remote_run_dir": remote_dir,
        "mask_semantics": "transparent_pixels_are_editable",
        "ia_window_size": list(ia_size),
        "provider_input_size": list(provider_size),
        "gateway_transport": "json_data_url_to_multipart",
    }
