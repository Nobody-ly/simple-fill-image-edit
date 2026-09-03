from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np
from PIL import Image


def read_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def read_mask(path: Path, size: tuple[int, int] | None = None) -> np.ndarray:
    mask = np.asarray(Image.open(path).convert("L"), dtype=np.uint8)
    if size and (mask.shape[1], mask.shape[0]) != size:
        mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
    return np.where(mask >= 127, 255, 0).astype(np.uint8)


def rectangle_mask(
    size: tuple[int, int], box: tuple[int, int, int, int], *, minimum_edge: int = 4,
) -> np.ndarray:
    """Build a deterministic full-resolution mask from a user-drawn rectangle."""
    width, height = size
    x_min, y_min, x_max, y_max = (int(value) for value in box)
    x_min = max(0, min(width, x_min))
    x_max = max(0, min(width, x_max))
    y_min = max(0, min(height, y_min))
    y_max = max(0, min(height, y_max))
    if x_max - x_min < minimum_edge or y_max - y_min < minimum_edge:
        raise ValueError("框选区域太小，请重新拖动框选")
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[y_min:y_max, x_min:x_max] = 255
    return mask


def dilate(mask: np.ndarray, kernel_size: int) -> np.ndarray:
    """Match upstream Inpaint Anything's square-kernel mask dilation."""
    if kernel_size <= 0:
        return mask
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    return cv2.dilate(mask, kernel, iterations=1)


def expand_mask(mask: np.ndarray, radius_px: int) -> np.ndarray:
    """Expand a binary mask outwards by an explicit pixel radius."""
    if radius_px <= 0:
        return mask.copy()
    diameter = radius_px * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter, diameter))
    return cv2.dilate(mask, kernel, iterations=1)


def erode_mask(mask: np.ndarray, radius_px: int) -> np.ndarray:
    """Contract a binary mask by an explicit pixel radius."""
    if radius_px <= 0:
        return mask.copy()
    diameter = radius_px * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter, diameter))
    return cv2.erode(mask, kernel, iterations=1)


def build_simple_fill_mask(
    target_mask: np.ndarray,
    *,
    cleanup_radius_px: int = 6,
    growth_ratio: float = 0.35,
) -> tuple[np.ndarray, dict]:
    """Create one Fill Anything mask with room for a larger new object."""
    binary = np.where(target_mask > 0, 255, 0).astype(np.uint8)
    x, y, width, height = cv2.boundingRect(binary)
    if width <= 0 or height <= 0:
        raise ValueError("目标蒙版为空")
    growth_radius = max(
        max(0, int(cleanup_radius_px)),
        int(round(max(width, height) * max(0.0, float(growth_ratio)))),
    )
    edit_mask = expand_mask(binary, growth_radius)
    return edit_mask, {
        "target_bbox": [int(x), int(y), int(width), int(height)],
        "cleanup_radius_px": int(cleanup_radius_px),
        "growth_ratio": round(float(growth_ratio), 4),
        "growth_radius_px": int(growth_radius),
        "coverage": round(float((edit_mask > 0).mean()), 6),
    }


def build_occlusion_masks(
    target_mask: np.ndarray,
    protection_masks: list[np.ndarray],
    generation_radius_px: int,
    *,
    protection_radius_px: int = 0,
    protection_underlap_px: int = 2,
    result_object_mask: np.ndarray | None = None,
    result_radius_px: int = 2,
) -> dict[str, np.ndarray]:
    """Build generation and commit masks for an occlusion-aware replacement."""
    envelope = expand_mask(target_mask, generation_radius_px)
    protection = np.zeros_like(target_mask, dtype=np.uint8)
    for item in protection_masks:
        protection = np.maximum(protection, item)
    protection = expand_mask(protection, protection_radius_px)
    # Let the generated layer extend a little underneath the exact foreground
    # cutout. The original foreground is restored after compositing. This is
    # the same underlap principle used by ordinary layered artwork and avoids
    # exposing pixels from the replaced object around hands, hair or straps.
    generation_guard = erode_mask(protection, protection_underlap_px)
    editable = np.where((envelope > 0) & (generation_guard == 0), 255, 0).astype(np.uint8)
    commit = editable.copy()
    if result_object_mask is not None:
        # Keep enough of the old-object area to erase residual outlines, then
        # add the newly segmented object. Clamp both to the approved envelope
        # and remove all protected foreground pixels.
        cleanup_radius = min(max(generation_radius_px, 4), 10)
        cleanup = expand_mask(target_mask, cleanup_radius)
        result_region = expand_mask(result_object_mask, result_radius_px)
        commit = np.where(
            ((cleanup > 0) | (result_region > 0)) & (editable > 0), 255, 0,
        ).astype(np.uint8)
    return {
        "envelope": envelope,
        "protection": protection,
        "generation_guard": generation_guard,
        "editable": editable,
        "commit": commit,
    }


def composite_occlusion_layers(
    original: np.ndarray,
    generated: np.ndarray,
    commit_mask: np.ndarray,
    foreground_restore_mask: np.ndarray,
    *,
    feather_px: int = 0,
) -> np.ndarray:
    """Commit generation, then restore the protected foreground interior."""
    result = feathered_composite(
        original, generated, commit_mask, feather_px=feather_px, operation="fill",
    )
    protected = foreground_restore_mask > 0
    result[protected] = original[protected]
    return result


def feathered_composite(original: np.ndarray, generated: np.ndarray,
                        mask: np.ndarray, feather_px: int = 0,
                        operation: str = "fill") -> np.ndarray:
    """Composite with an inward feather while keeping outside pixels exact."""
    h, w = original.shape[:2]
    if generated.shape[:2] != (h, w):
        generated = cv2.resize(generated, (w, h), interpolation=cv2.INTER_LANCZOS4)
    binary = np.where(mask > 0, 1, 0).astype(np.uint8)
    if operation == "replace_background":
        binary = 1 - binary
    if feather_px > 0:
        distance = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
        alpha_2d = np.clip(distance / float(feather_px), 0.0, 1.0)
    else:
        alpha_2d = binary.astype(np.float32)
    alpha = alpha_2d[:, :, None]
    result = original.astype(np.float32) * (1.0 - alpha) + generated.astype(np.float32) * alpha
    return np.clip(result, 0, 255).astype(np.uint8)


def composite(original: np.ndarray, generated: np.ndarray,
              mask: np.ndarray, operation: str) -> np.ndarray:
    h, w = original.shape[:2]
    if generated.shape[:2] != (h, w):
        generated = cv2.resize(generated, (w, h), interpolation=cv2.INTER_LANCZOS4)
    alpha = (mask.astype(np.float32) / 255.0)[:, :, None]
    if operation == "replace_background":
        alpha = 1.0 - alpha
    result = original.astype(np.float32) * (1.0 - alpha) + generated.astype(np.float32) * alpha
    return np.clip(result, 0, 255).astype(np.uint8)


def mask_preview(original: np.ndarray, mask: np.ndarray) -> np.ndarray:
    color = np.zeros_like(original)
    color[:, :, 0] = 18
    color[:, :, 1] = 196
    color[:, :, 2] = 142
    alpha = (mask.astype(np.float32) / 255.0 * 0.58)[:, :, None]
    return np.clip(original * (1.0 - alpha) + color * alpha, 0, 255).astype(np.uint8)
