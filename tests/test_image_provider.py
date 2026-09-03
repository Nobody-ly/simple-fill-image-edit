import numpy as np

from app.image_provider import _data_url, build_alpha_mask


def test_native_mask_alpha_is_transparent_where_editable():
    editable = np.zeros((4, 5), dtype=np.uint8)
    editable[1:3, 2:4] = 255

    alpha = np.asarray(build_alpha_mask(editable))[:, :, 3]

    assert np.all(alpha[editable == 255] == 0)
    assert np.all(alpha[editable == 0] == 255)


def test_json_transport_uses_png_data_urls():
    value = _data_url(b"png-bytes")
    assert value.startswith("data:image/png;base64,")
    assert "cG5nLWJ5dGVz" in value
