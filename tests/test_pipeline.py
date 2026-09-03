import json

import numpy as np
from PIL import Image

from app import pipeline
from app.pipeline import SimpleFillOptions, run_simple_fill


def test_fixed_pipeline_preserves_every_pixel_outside_effective_mask(tmp_path, monkeypatch):
    source = np.zeros((640, 800, 3), dtype=np.uint8)
    source[:, :] = (40, 80, 120)
    mask = np.zeros((640, 800), dtype=np.uint8)
    mask[260:380, 340:460] = 255
    source_path = tmp_path / "source.png"
    mask_path = tmp_path / "mask.png"
    Image.fromarray(source).save(source_path)
    Image.fromarray(mask).save(mask_path)

    def fake_edit(source_crop, crop_mask, prompt, output_dir, progress):
        crop = np.asarray(Image.open(source_crop).convert("RGB"), dtype=np.uint8).copy()
        crop[crop_mask > 0] = (230, 20, 90)
        output = output_dir / "image-edit-provider-original.png"
        Image.fromarray(crop).save(output)
        progress("fake provider completed", 82)
        return output, {"provider": "fake", "model": "test"}

    monkeypatch.setattr(pipeline, "edit_image", fake_edit)
    result_path, report = run_simple_fill(
        source_path,
        mask_path,
        "replace object",
        tmp_path / "run",
        options=SimpleFillOptions(dilation_px=6, growth_ratio=0.2, feather_px=3),
    )

    result = np.asarray(Image.open(result_path).convert("RGB"))
    effective = np.asarray(Image.open(tmp_path / "run" / "edit-mask.png").convert("L"))
    assert result.shape == source.shape
    assert np.array_equal(result[effective == 0], source[effective == 0])
    assert report["outside_edit_mask_changed_pixels"] == 0
    saved = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert saved["contract"] == "simple-fill.v1"
