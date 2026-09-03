from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import storage  # noqa: E402
from app.compositor import read_mask, read_rgb  # noqa: E402


def register(project_id: str, source_task_id: str) -> dict:
    project = storage.read_project(project_id)
    source_task = storage.read_task(project_id, source_task_id)
    source_task_dir = storage.project_dir(project_id) / "tasks" / source_task_id
    candidate_path = source_task_dir / "layered-candidate-full.png"
    effective_path = source_task_dir / "effective-mask.png"
    guard_path = source_task_dir / "generation-protection-guard.png"
    if not candidate_path.is_file() or not effective_path.is_file() or not guard_path.is_file():
        raise RuntimeError(f"The source task has no reusable native-mask candidate: {source_task_id}")

    target_meta = next(
        item for item in project.get("masks", []) if item["id"] == source_task["mask_id"]
    )
    source_ref = target_meta.get("source_ref", "source")
    if source_ref == "source":
        source_path = storage.project_dir(project_id) / "source.png"
    else:
        source_version = next(item for item in project["versions"] if item["id"] == source_ref)
        source_path = storage.project_dir(project_id) / "versions" / source_version["filename"]

    original = read_rgb(source_path)
    candidate = read_rgb(candidate_path)
    size = (original.shape[1], original.shape[0])
    effective = read_mask(effective_path, size) > 0
    guard = read_mask(guard_path, size) > 0
    changed = np.max(
        np.abs(candidate.astype(np.int16) - original.astype(np.int16)), axis=2
    ) > 0
    report = {
        "contract": "catsco.native-mask-candidate-quality.v1",
        "changed_pixels": int(np.count_nonzero(changed)),
        "outside_effective_changed_pixels": int(np.count_nonzero(changed & ~effective)),
        "protected_changed_pixels": int(np.count_nonzero(changed & guard)),
        "effective_mask_coverage": round(float(effective.mean()), 6),
    }
    report["safety_passed"] = (
        report["outside_effective_changed_pixels"] == 0
        and report["protected_changed_pixels"] == 0
    )
    if not report["safety_passed"]:
        raise RuntimeError(f"Candidate safety check failed: {report}")

    derived = storage.create_task(
        project_id,
        source_task["operation"],
        source_task["prompt"],
        source_task["mask_id"],
        source_task["dilation"],
        source_task.get("feather", 5),
        source_task.get("protected_mask_ids", []),
        source_task.get("result_object_prompt", ""),
        source_task.get("pipeline_mode", "legacy"),
        source_task.get("cleanup_radius", 10),
        source_task.get("semantic_edge", 6),
    )
    task_id = derived["id"]
    task_dir = storage.project_dir(project_id) / "tasks" / task_id
    shutil.copy2(candidate_path, task_dir / "native-mask-candidate.png")
    (task_dir / "quality-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    version_id = storage.new_id("ver")
    filename = f"{version_id}.png"
    shutil.copy2(candidate_path, storage.project_dir(project_id) / "versions" / filename)
    project = storage.read_project(project_id)
    project["versions"].insert(0, {
        "id": version_id,
        "filename": filename,
        "task_id": task_id,
        "operation": source_task["operation"],
        "prompt": source_task["prompt"],
        "mask_id": source_task["mask_id"],
        "protected_mask_ids": source_task.get("protected_mask_ids", []),
        "result_object_prompt": source_task.get("result_object_prompt", ""),
        "pipeline_mode": source_task.get("pipeline_mode", "legacy"),
        "source_ref": source_ref,
        "width": int(candidate.shape[1]),
        "height": int(candidate.shape[0]),
        "created_at": storage.now_iso(),
        "reprocess_of_task": source_task_id,
        "reused_paid_generation": True,
        "composition_policy": "native-mask-full-candidate",
    })
    storage.write_project(project)
    storage.update_task(
        project_id,
        task_id,
        status="completed",
        stage="已采用 Image2 原生蒙版候选（未重复付费生成）",
        progress=100,
        version_id=version_id,
        reprocess_of=source_task_id,
        reused_paid_generation=True,
        composition_policy="native-mask-full-candidate",
        artifacts={
            "result": f"../../versions/{filename}",
            "native_mask_candidate": "native-mask-candidate.png",
            "quality_report": "quality-report.json",
            "provider_record": {
                "provider": "reused-image2-candidate",
                "source_task_id": source_task_id,
                "post_segmentation": "disabled",
            },
        },
        error=None,
    )
    return {
        "source_task_id": source_task_id,
        "task_id": task_id,
        "version_id": version_id,
        "result_path": str(storage.project_dir(project_id) / "versions" / filename),
        "quality_report": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--task", action="append", required=True)
    args = parser.parse_args()
    print(json.dumps(
        [register(args.project, task_id) for task_id in args.task],
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
