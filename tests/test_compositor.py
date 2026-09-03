import numpy as np

from app.compositor import build_simple_fill_mask, feathered_composite


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
