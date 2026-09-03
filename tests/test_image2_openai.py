from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import base64
import json
import sys
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.compositor import (
    build_occlusion_masks, composite_occlusion_layers, expand_mask,
    feathered_composite,
)
from app.image2_openai import build_alpha_mask, run_masked_image2


class _Handler(BaseHTTPRequestHandler):
    body = b""
    headers_seen = None

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        type(self).body = self.rfile.read(length)
        type(self).headers_seen = self.headers
        image = Image.new("RGB", (1024, 1024), "white")
        stream = BytesIO()
        image.save(stream, format="PNG")
        payload = json.dumps({
            "data": [{"b64_json": base64.b64encode(stream.getvalue()).decode("ascii")}]
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("x-request-id", "mock-request")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


class MaskedImage2Tests(unittest.TestCase):
    def test_occlusion_masks_protect_foreground_and_allow_new_shape(self):
        target = np.zeros((40, 40), dtype=np.uint8)
        target[16:24, 16:24] = 255
        protection = np.zeros((40, 40), dtype=np.uint8)
        protection[8:32, 20:23] = 255
        result_object = np.zeros((40, 40), dtype=np.uint8)
        result_object[12:28, 12:29] = 255

        masks = build_occlusion_masks(
            target,
            [protection],
            generation_radius_px=8,
            protection_radius_px=0,
            protection_underlap_px=2,
            result_object_mask=result_object,
            result_radius_px=0,
        )

        self.assertTrue(np.any(masks["editable"][protection > 0]))
        self.assertFalse(np.any(masks["editable"][masks["generation_guard"] > 0]))
        self.assertEqual(int(masks["commit"][13, 13]), 255)
        self.assertEqual(int(masks["commit"][2, 2]), 0)
        self.assertTrue(np.all(masks["commit"][masks["envelope"] == 0] == 0))

        original = np.full((40, 40, 3), 30, dtype=np.uint8)
        generated = np.full((40, 40, 3), 220, dtype=np.uint8)
        composed = composite_occlusion_layers(
            original, generated, masks["commit"], masks["generation_guard"], feather_px=0,
        )
        self.assertTrue(np.array_equal(
            composed[masks["generation_guard"] > 0],
            original[masks["generation_guard"] > 0],
        ))
        contact_edge = (masks["protection"] > 0) & (masks["generation_guard"] == 0)
        self.assertTrue(np.any(composed[contact_edge] != original[contact_edge]))

    def test_expansion_uses_explicit_pixel_radius(self):
        mask = np.zeros((21, 21), dtype=np.uint8)
        mask[10, 10] = 255
        expanded = expand_mask(mask, 4)
        ys, xs = np.where(expanded > 0)
        self.assertEqual((int(xs.min()), int(xs.max())), (6, 14))
        self.assertEqual((int(ys.min()), int(ys.max())), (6, 14))

    def test_feather_keeps_every_outside_pixel_exact(self):
        original = np.full((20, 20, 3), 20, dtype=np.uint8)
        generated = np.full((20, 20, 3), 220, dtype=np.uint8)
        mask = np.zeros((20, 20), dtype=np.uint8)
        mask[4:16, 4:16] = 255
        result = feathered_composite(original, generated, mask, feather_px=4)
        self.assertTrue(np.array_equal(result[mask == 0], original[mask == 0]))
        self.assertTrue(np.array_equal(result[10, 10], generated[10, 10]))
        self.assertTrue(np.all(result[4, 10] > original[4, 10]))
        self.assertTrue(np.all(result[4, 10] < generated[4, 10]))

    def test_alpha_mask_makes_selected_pixels_transparent(self):
        mask = np.zeros((4, 4), dtype=np.uint8)
        mask[1:3, 1:3] = 255
        rgba = np.asarray(build_alpha_mask(mask))
        self.assertEqual(int(rgba[1, 1, 3]), 0)
        self.assertEqual(int(rgba[0, 0, 3]), 255)

    def test_openai_compatible_multipart_has_real_mask_field(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old = {
            "masked_image2_base_url": settings.masked_image2_base_url,
            "masked_image2_api_key": settings.masked_image2_api_key,
            "masked_image2_api_key_file": settings.masked_image2_api_key_file,
        }
        try:
            object.__setattr__(settings, "masked_image2_base_url", f"http://127.0.0.1:{server.server_port}/v1")
            object.__setattr__(settings, "masked_image2_api_key", "test-only")
            object.__setattr__(settings, "masked_image2_api_key_file", None)
            with TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / "source.png"
                Image.new("RGB", (512, 512), "gray").save(source)
                mask = np.zeros((512, 512), dtype=np.uint8)
                mask[180:330, 180:330] = 255
                output, record = run_masked_image2(
                    source, mask, "一只白猫", root, lambda *_args: None,
                )
                self.assertTrue(output.is_file())
                self.assertEqual(record["provider_input_size"], [1024, 1024])
                self.assertEqual(record["ia_window_size"], [512, 512])
            body = _Handler.body
            self.assertIn(b'name="mask"', body)
            self.assertIn(b'name="image[]"', body)
            self.assertIn(b'name="model"', body)
            self.assertEqual(_Handler.headers_seen["Authorization"], "Bearer test-only")
        finally:
            server.shutdown()
            server.server_close()
            for name, value in old.items():
                object.__setattr__(settings, name, value)


if __name__ == "__main__":
    unittest.main()
