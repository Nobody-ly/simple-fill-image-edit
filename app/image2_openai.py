from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Callable
import base64
import json
from urllib.parse import urlparse

import numpy as np
import requests
from PIL import Image

from .config import settings


Progress = Callable[[str, int], None]


def _api_key() -> str:
    if settings.masked_image2_api_key:
        return settings.masked_image2_api_key
    path = settings.masked_image2_api_key_file
    if path and path.is_file():
        return path.read_text(encoding="utf-8").strip()
    raise RuntimeError(
        "原生 mask Image2 尚未配置 API Key；请设置 "
        "CATSCO_MASKED_IMAGE2_API_KEY 或 CATSCO_MASKED_IMAGE2_API_KEY_FILE"
    )


def _endpoint() -> str:
    value = settings.masked_image2_base_url.rstrip("/")
    if value.endswith("/images/edits"):
        endpoint = value
    else:
        endpoint = f"{value}/images/edits"
    parsed = urlparse(endpoint)
    if parsed.scheme == "https":
        return endpoint
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return endpoint
    raise RuntimeError("原生 mask Image2 接口必须使用 HTTPS；仅本机回环地址允许 HTTP")


def build_alpha_mask(mask: np.ndarray) -> Image.Image:
    """Convert IA white=edit mask to OpenAI transparent=edit RGBA mask."""
    binary = np.where(mask >= 127, 255, 0).astype(np.uint8)
    alpha = 255 - binary
    rgba = np.full((binary.shape[0], binary.shape[1], 4), 255, dtype=np.uint8)
    rgba[:, :, 3] = alpha
    return Image.fromarray(rgba)


def _decode_result(response: requests.Response, task_dir: Path) -> Path:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"原生 mask Image2 返回非 JSON（HTTP {response.status_code}）"
        ) from exc
    if not response.ok:
        message = payload.get("error", payload)
        if isinstance(message, dict):
            message = message.get("message") or json.dumps(message, ensure_ascii=False)
        raise RuntimeError(f"原生 mask Image2 HTTP {response.status_code}: {message}")
    data = payload.get("data") or []
    if not data:
        raise RuntimeError("原生 mask Image2 结果缺少 data[0]")
    item = data[0]
    output = task_dir / "image2-native-mask-provider-original.png"
    if item.get("b64_json"):
        try:
            output.write_bytes(base64.b64decode(item["b64_json"], validate=True))
        except Exception as exc:
            raise RuntimeError("原生 mask Image2 返回了无效的 b64_json") from exc
        return output
    url = item.get("url")
    if not url:
        raise RuntimeError("原生 mask Image2 结果既没有 b64_json 也没有 url")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError("原生 mask Image2 返回了不安全的结果 URL")
    downloaded = requests.get(url, timeout=120)
    downloaded.raise_for_status()
    output.write_bytes(downloaded.content)
    return output


def run_masked_image2(
    source: Path,
    mask: np.ndarray,
    prompt: str,
    task_dir: Path,
    progress: Progress,
) -> tuple[Path, dict]:
    key = _api_key()
    with Image.open(source) as image:
        source_png = image.convert("RGBA")
    if mask.shape != (source_png.height, source_png.width):
        raise RuntimeError("原生 mask Image2 的底图与蒙版尺寸不一致")
    original_size = source_png.size
    # GPT Image 2 currently requires at least 655,360 output pixels. IA's
    # upstream Fill Anything window is exactly 512x512, so use a 1024x1024
    # transport canvas and map the provider result back to the IA window.
    provider_size = original_size
    if source_png.width * source_png.height < 655_360:
        provider_size = (1024, 1024)
        source_png = source_png.resize(provider_size, Image.Resampling.LANCZOS)
        mask = np.asarray(
            Image.fromarray(mask).resize(provider_size, Image.Resampling.NEAREST),
            dtype=np.uint8,
        )
    alpha_mask = build_alpha_mask(mask)
    source_bytes = BytesIO()
    mask_bytes = BytesIO()
    source_png.save(source_bytes, format="PNG")
    alpha_mask.save(mask_bytes, format="PNG")
    source_payload = source_bytes.getvalue()
    mask_payload = mask_bytes.getvalue()
    mask_path = task_dir / "image2-native-alpha-mask.png"
    mask_path.write_bytes(mask_payload)

    progress("正在提交 Image2 原生透明蒙版编辑", 46)
    response = requests.post(
        _endpoint(),
        headers={"Authorization": f"Bearer {key}"},
        data={
            "model": settings.masked_image2_model,
            "prompt": (
                "只修改透明蒙版指定的局部区域，保留其余构图、人物、姿态、透视、"
                "光照和像素级视觉关系。让修改内容在边界处与原图自然衔接。"
                f"目标内容：{prompt.strip()}"
            ),
            "size": f"{source_png.width}x{source_png.height}",
            "quality": "high",
            "output_format": "png",
        },
        files={
            settings.masked_image2_image_field: ("source.png", source_payload, "image/png"),
            "mask": ("mask.png", mask_payload, "image/png"),
        },
        timeout=settings.masked_image2_timeout,
    )
    request_id = response.headers.get("x-request-id")
    output = _decode_result(response, task_dir)
    progress("Image2 原生蒙版结果已返回", 82)
    return output, {
        "provider": "gpt-image-2-native-mask",
        "endpoint_origin": urlparse(_endpoint()).netloc,
        "endpoint_path": urlparse(_endpoint()).path,
        "model": settings.masked_image2_model,
        "request_id": request_id,
        "mask_semantics": "transparent_pixels_are_editable",
        "mask_file": mask_path.name,
        "ia_window_size": list(original_size),
        "provider_input_size": list(provider_size),
    }
