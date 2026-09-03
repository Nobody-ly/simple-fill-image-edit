import numpy as np

import pytest

from app.compositor import build_simple_fill_mask, feathered_composite, rectangle_mask


def test_rectangle_mask_uses_exact_full_resolution_box():
    mask = rectangle_mask((120, 100), (25, 20, 75, 60))

    assert mask.shape == (100, 120)
    assert np.count_nonzero(mask) == 50 * 40
    assert np.all(mask[20:60, 25:75] == 255)
    assert np.count_nonzero(mask[:20]) == 0
    assert np.count_nonzero(mask[:, :25]) == 0


def test_rectangle_mask_clamps_to_image_and_rejects_tiny_regions():
    mask = rectangle_mask((40, 30), (-20, -10, 12, 8))
    assert np.count_nonzero(mask) == 12 * 8

    with pytest.raises(ValueError, match="框选区域太小"):
        rectangle_mask((40, 30), (10, 10, 12, 12))


def test_mask_expansion_reserves_room_and_records_parameters():
    target = np.zeros((100, 120), dtype=np.uint8)
    target[40:60, 50:70] = 255

    edit, record = build_simple_fill_mask(
        target, cleanup_radius_px=6, growth_ratio=0.35
    )

    assert np.count_nonzero(edit) > np.count_nonzero(target)
    assert record["target_bbox"] == [50, 40, 20, 20]
    assert record["growth_radius_px"] == 7


def test_feather_never_changes_pixels_outside_mask():
    original = np.zeros((80, 80, 3), dtype=np.uint8)
    generated = np.full_like(original, 255)
    mask = np.zeros((80, 80), dtype=np.uint8)
    mask[20:60, 20:60] = 255

    result = feathered_composite(original, generated, mask, feather_px=5)

    assert np.array_equal(result[mask == 0], original[mask == 0])
    assert np.any(result[mask > 0] != original[mask > 0])
