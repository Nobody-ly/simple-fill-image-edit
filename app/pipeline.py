from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
import json

import cv2
import numpy as np
from PIL import Image

from .compositor import build_simple_fill_mask, feathered_composite, read_mask, read_rgb
from .image_provider import edit_image
from .inpaint_anything import prepare_crop, restore_crop


Progress = Callable[[str, int], None]


@dataclass(frozen=True)
class SimpleFillOptions:
    dilation_px: int = 6
    growth_ratio: float = 0.35
    feather_px: int = 3
    crop_size: int = 512


def run_simple_fill(
    source_path: Path,
    mask_path: Path,
    prompt: str,
    output_dir: Path,
    *,
    options: SimpleFillOptions = SimpleFillOptions(),
    progress: Progress = lambda _stage, _value: None,
) -> tuple[Path, dict]:
    """Run the fixed-mask Simple Fill pipeline without mid-run agent decisions."""
    output_dir.mkdir(parents=True, exist_ok=True)
    original = read_rgb(source_path)
    target_mask = read_mask(mask_path, (original.shape[1], original.shape[0]))
    edit_mask, mask_record = build_simple_fill_mask(
        target_mask,
        cleanup_radius_px=options.dilation_px,
        growth_ratio=options.growth_ratio,
    )
    Image.fromarray(target_mask).save(output_dir / "target-mask.png")
    Image.fromarray(edit_mask).save(output_dir / "edit-mask.png")
    progress("inputs and mask locked", 18)

    crop_image, crop_mask = prepare_crop(original, edit_mask, crop_size=options.crop_size)
    crop_path = output_dir / "inpaint-anything-crop.png"
    Image.fromarray(crop_image).save(crop_path)
    Image.fromarray(crop_mask).save(output_dir / "inpaint-anything-crop-mask.png")
    provider_path, provider_record = edit_image(
        crop_path, crop_mask, prompt, output_dir, progress
    )
    generated_crop = read_rgb(provider_path)
    if generated_crop.shape[:2] != crop_image.shape[:2]:
        generated_crop = cv2.resize(
            generated_crop,
            (crop_image.shape[1], crop_image.shape[0]),
            interpolation=cv2.INTER_LANCZOS4,
        )
    Image.fromarray(generated_crop).save(output_dir / "generated-crop-normalized.png")
    progress("restoring Inpaint Anything crop", 86)

    candidate = restore_crop(
        original.copy(), edit_mask.copy(), generated_crop, crop_size=options.crop_size
    )
    Image.fromarray(candidate).save(output_dir / "candidate-full.png")
    result = feathered_composite(
        original, candidate, edit_mask, feather_px=options.feather_px, operation="fill"
    )
    result_path = output_dir / "result.png"
    Image.fromarray(result).save(result_path)
    report = {
        "contract": "simple-fill.v1",
        "prompt": prompt,
        "options": asdict(options),
        "mask": mask_record,
        "provider": provider_record,
        "outside_edit_mask_changed_pixels": int(np.count_nonzero(
            np.any(result != original, axis=2) & (edit_mask == 0)
        )),
    }
    (output_dir / "run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    progress("completed", 100)
    return result_path, report
