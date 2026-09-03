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


def _endpoint() -> str:
    value = settings.image_api_base_url.rstrip("/")
    endpoint = value if value.endswith("/images/edits") else f"{value}/images/edits"
    parsed = urlparse(endpoint)
    if parsed.scheme == "https":
        return endpoint
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return endpoint
    raise RuntimeError("IMAGE_API_BASE_URL must use HTTPS; HTTP is allowed only for localhost")


def build_alpha_mask(mask: np.ndarray) -> Image.Image:
    """Convert white=editable mask to transparent=editable RGBA mask."""
    binary = np.where(mask >= 127, 255, 0).astype(np.uint8)
    rgba = np.full((binary.shape[0], binary.shape[1], 4), 255, dtype=np.uint8)
    rgba[:, :, 3] = 255 - binary
    return Image.fromarray(rgba)


def _decode(response: requests.Response, output: Path) -> None:
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"image edit returned non-JSON HTTP {response.status_code}") from exc
    if not response.ok:
        error = body.get("error", body)
        if isinstance(error, dict):
            error = error.get("message") or json.dumps(error, ensure_ascii=False)
        raise RuntimeError(f"image edit HTTP {response.status_code}: {error}")
    items = body.get("data") or []
    if not items:
        raise RuntimeError("image edit response has no data[0]")
    item = items[0]
    if item.get("b64_json"):
        output.write_bytes(base64.b64decode(item["b64_json"], validate=True))
        return
    url = item.get("url")
    if not url or urlparse(url).scheme != "https":
        raise RuntimeError("image edit response has neither b64_json nor an HTTPS URL")
    downloaded = requests.get(url, timeout=(10, 120))
    downloaded.raise_for_status()
    output.write_bytes(downloaded.content)


def edit_image(
    source: Path,
    mask: np.ndarray,
    prompt: str,
    output_dir: Path,
    progress: Progress,
) -> tuple[Path, dict]:
    """Call an OpenAI-compatible /images/edits endpoint with a native alpha mask."""
    if not settings.image_api_key:
        raise RuntimeError("IMAGE_API_KEY is not configured")
    with Image.open(source) as opened:
        source_image = opened.convert("RGBA")
    if mask.shape != (source_image.height, source_image.width):
        raise RuntimeError("source and mask dimensions do not match")

    original_size = source_image.size
    provider_size = original_size
    if source_image.width * source_image.height < 655_360:
        provider_size = (1024, 1024)
        source_image = source_image.resize(provider_size, Image.Resampling.LANCZOS)
        mask = np.asarray(
            Image.fromarray(mask).resize(provider_size, Image.Resampling.NEAREST),
            dtype=np.uint8,
        )

    source_buffer = BytesIO()
    mask_buffer = BytesIO()
    source_image.save(source_buffer, "PNG")
    build_alpha_mask(mask).save(mask_buffer, "PNG")
    mask_path = output_dir / "image-edit-alpha-mask.png"
    mask_path.write_bytes(mask_buffer.getvalue())

    system_guard = (
        "Edit only the transparent mask region. Preserve all pixels, composition, identity, "
        "pose, perspective and lighting outside it. Blend the edited boundary naturally. "
    )
    progress("submitting native-mask image edit", 46)
    response = requests.post(
        _endpoint(),
        headers={"Authorization": f"Bearer {settings.image_api_key}"},
        data={
            "model": settings.image_model,
            "prompt": system_guard + "Target: " + prompt.strip(),
            "size": f"{source_image.width}x{source_image.height}",
            "quality": "high",
            "output_format": "png",
        },
        files={
            settings.image_field: ("source.png", source_buffer.getvalue(), "image/png"),
            "mask": ("mask.png", mask_buffer.getvalue(), "image/png"),
        },
        timeout=settings.image_timeout_seconds,
    )
    output = output_dir / "image-edit-provider-original.png"
    _decode(response, output)
    progress("image edit completed", 82)
    return output, {
        "provider": "openai-compatible-native-mask",
        "model": settings.image_model,
        "request_id": response.headers.get("x-request-id"),
        "endpoint_origin": urlparse(_endpoint()).netloc,
        "mask_semantics": "transparent_pixels_are_editable",
        "crop_size": list(original_size),
        "provider_input_size": list(provider_size),
    }
