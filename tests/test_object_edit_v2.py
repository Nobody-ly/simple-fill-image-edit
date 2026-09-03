from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sys
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.object_edit_v2 import (
    alpha_composite_with_foreground,
    build_clean_plate_mask,
    build_quality_report,
    build_semantic_alpha,
    segment_changed_object,
)
from app import main, storage
from app.config import settings


class ObjectEditV2Tests(unittest.TestCase):
    def test_clean_plate_expands_old_object_but_keeps_protected_interior(self):
        target = np.zeros((48, 48), dtype=np.uint8)
        target[18:30, 18:30] = 255
        protected = np.zeros_like(target)
        protected[8:40, 23:27] = 255
        cleanup = build_clean_plate_mask(
            target, protected, cleanup_radius_px=6,
        )
        self.assertEqual(int(cleanup[15, 15]), 255)
        self.assertEqual(int(cleanup[24, 24]), 0)
        self.assertEqual(int(cleanup[2, 2]), 0)

    def test_semantic_alpha_allows_new_shape_and_stays_inside_editable(self):
        target = np.zeros((64, 64), dtype=np.uint8)
        target[25:39, 25:39] = 255
        result_object = np.zeros_like(target)
        result_object[16:48, 20:46] = 255
        editable = np.zeros_like(target)
        editable[10:54, 10:54] = 255
        commit, alpha, trimap = build_semantic_alpha(
            target, result_object, editable,
            cleanup_radius_px=5, result_radius_px=2, edge_width_px=5,
        )
        self.assertEqual(int(commit[17, 21]), 255)
        self.assertFalse(np.any(alpha[editable == 0]))
        self.assertTrue(np.any(trimap == 128))
        self.assertTrue(np.any(trimap == 255))

    def test_local_change_fallback_keeps_target_connected_change(self):
        baseline = np.zeros((80, 80, 3), dtype=np.uint8)
        candidate = baseline.copy()
        candidate[20:55, 25:60] = 220
        candidate[5:10, 5:10] = 220
        editable = np.full((80, 80), 255, dtype=np.uint8)
        target = np.zeros((80, 80), dtype=np.uint8)
        target[35:45, 35:45] = 255
        mask, record = segment_changed_object(
            baseline, candidate, editable, target,
        )
        self.assertEqual(int(mask[35, 35]), 255)
        self.assertEqual(int(mask[7, 7]), 0)
        self.assertEqual(record["provider"], "local-change-fallback")

    def test_composite_is_exact_outside_and_on_protected_foreground(self):
        original = np.full((40, 40, 3), 25, dtype=np.uint8)
        candidate = np.full((40, 40, 3), 230, dtype=np.uint8)
        alpha = np.zeros((40, 40), dtype=np.uint8)
        alpha[8:32, 8:32] = 255
        protected = np.zeros_like(alpha)
        protected[5:35, 19:22] = 255
        result = alpha_composite_with_foreground(
            original, candidate, alpha, protected,
        )
        self.assertTrue(np.array_equal(result[alpha == 0], original[alpha == 0]))
        self.assertTrue(np.array_equal(result[protected > 0], original[protected > 0]))
        self.assertTrue(np.array_equal(result[15, 15], candidate[15, 15]))

        envelope = np.where(alpha > 0, 255, 0).astype(np.uint8)
        report = build_quality_report(
            original, result, alpha, envelope, protected,
        )
        self.assertTrue(report["safety_passed"])
        self.assertEqual(report["outside_changed_pixels"], 0)
        self.assertEqual(report["protected_changed_pixels"], 0)

    def test_integrated_v2_task_persists_every_review_artifact(self):
        old_data_dir = settings.data_dir
        with TemporaryDirectory() as folder:
            object.__setattr__(settings, "data_dir", Path(folder))
            try:
                project = storage.create_project("v2 test", "source.png", 512, 512)
                source = np.full((512, 512, 3), 180, dtype=np.uint8)
                source[190:322, 190:322] = (35, 55, 75)
                Image.fromarray(source).save(storage.project_dir(project["id"]) / "source.png")

                mask = np.zeros((512, 512), dtype=np.uint8)
                mask[190:322, 190:322] = 255
                mask_id = storage.new_id("mask")
                Image.fromarray(mask).save(
                    storage.project_dir(project["id"]) / "masks" / f"{mask_id}.png"
                )
                project = storage.read_project(project["id"])
                project["masks"].append({
                    "id": mask_id,
                    "source_ref": "source",
                    "prompt": "旧书",
                    "coverage": float((mask > 0).mean()),
                })
                storage.write_project(project)
                task = storage.create_task(
                    project["id"], "fill", "一只白猫", mask_id,
                    32, 5, [], "白猫", "object_v2", 10, 6,
                )

                def fake_lama(image, edit_mask):
                    result = image.copy()
                    result[edit_mask > 0] = (180, 180, 180)
                    return result

                def fake_image2(_task_id, crop_source, _crop_mask, _prompt,
                                task_dir, progress):
                    progress("mock image2", 70)
                    generated = np.asarray(Image.open(crop_source).convert("RGB")).copy()
                    generated[120:392, 150:362] = (240, 210, 160)
                    output = task_dir / "mock-provider.png"
                    Image.fromarray(generated).save(output)
                    return output, {"provider": "mock-image2"}

                result_mask = np.zeros((512, 512), dtype=np.uint8)
                result_mask[150:362, 175:337] = 255
                with patch.object(main, "lama_inpaint", side_effect=fake_lama), \
                     patch.object(main, "run_catsco_masked_image2", side_effect=fake_image2), \
                     patch.object(main, "sam3_segment", return_value=(result_mask, {"provider": "mock-sam3"})):
                    main._run_task(project["id"], task["id"])

                finished = storage.read_task(project["id"], task["id"])
                self.assertEqual(finished["status"], "completed", finished.get("error"))
                self.assertEqual(finished["pipeline_mode"], "object_v2")
                for name in (
                    "clean_plate", "clean_plate_mask", "layered_candidate_full",
                    "result_object_mask", "commit_alpha", "commit_trimap",
                    "quality_report",
                ):
                    self.assertIn(name, finished["artifacts"])
                    self.assertTrue(
                        (storage.project_dir(project["id"]) / "tasks" / task["id"]
                         / finished["artifacts"][name]).is_file()
                    )
            finally:
                object.__setattr__(settings, "data_dir", old_data_dir)


if __name__ == "__main__":
    unittest.main()
