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
from app.compositor import (  # noqa: E402
    build_occlusion_masks,
    composite_occlusion_layers,
    mask_preview,
    read_mask,
    read_rgb,
)
from app.object_edit_v2 import (  # noqa: E402
    alpha_composite_with_foreground,
    build_quality_report,
    build_semantic_alpha,
)
from app.sam3_wavespeed import segment as sam3_segment  # noqa: E402


def mask_record(project: dict, mask_id: str) -> dict:
    record = next((item for item in project.get("masks", []) if item["id"] == mask_id), None)
    if record is None:
        raise RuntimeError(f"Mask not found: {mask_id}")
    return record


def resolve_source(project: dict, source_ref: str) -> Path:
    folder = storage.project_dir(project["id"])
    if source_ref == "source":
        return folder / "source.png"
    version = next(
        (item for item in project.get("versions", []) if item["id"] == source_ref),
        None,
    )
    if version is None:
        raise RuntimeError(f"Source version not found: {source_ref}")
    return folder / "versions" / version["filename"]


def reprocess(project_id: str, source_task_id: str) -> dict:
    project = storage.read_project(project_id)
    source_task = storage.read_task(project_id, source_task_id)
    source_task_dir = storage.project_dir(project_id) / "tasks" / source_task_id
    candidate_path = source_task_dir / "layered-candidate-full.png"
    if not candidate_path.is_file():
        raise RuntimeError(f"Candidate image is missing: {candidate_path}")

    result_prompt = source_task.get("result_object_prompt", "").strip()
    if not result_prompt:
        raise RuntimeError("The source task has no result_object_prompt")

    target_meta = mask_record(project, source_task["mask_id"])
    source_ref = target_meta.get("source_ref", "source")
    source_path = resolve_source(project, source_ref)
    original = read_rgb(source_path)
    size = (original.shape[1], original.shape[0])
    target_mask = read_mask(
        storage.project_dir(project_id) / "masks" / f"{source_task['mask_id']}.png",
        size,
    )
    protected_masks = [
        read_mask(storage.project_dir(project_id) / "masks" / f"{mask_id}.png", size)
        for mask_id in source_task.get("protected_mask_ids", [])
    ]
    candidate = read_rgb(candidate_path)

    derived = storage.create_task(
        project_id,
        source_task["operation"],
        source_task["prompt"],
        source_task["mask_id"],
        source_task["dilation"],
        source_task.get("feather", 5),
        source_task.get("protected_mask_ids", []),
        result_prompt,
        source_task.get("pipeline_mode", "legacy"),
        source_task.get("cleanup_radius", 10),
        source_task.get("semantic_edge", 6),
    )
    task_id = derived["id"]
    task_dir = storage.project_dir(project_id) / "tasks" / task_id
    shutil.copy2(candidate_path, task_dir / "layered-candidate-full.png")
    storage.update_task(
        project_id,
        task_id,
        status="generating",
        stage="正在用 SAM3 重新识别已生成对象（不重复调用 Image2）",
        progress=30,
        reprocess_of=source_task_id,
        reused_paid_generation=True,
    )

    try:
        result_mask, sam_record = sam3_segment(
            task_dir / "layered-candidate-full.png",
            points=[],
            boxes=[],
            prompt=result_prompt,
        )
        coverage = float((result_mask > 0).mean())
        if coverage <= 0 or coverage > 0.65:
            raise RuntimeError(f"Untrusted SAM3 coverage: {coverage:.2%}")

        Image.fromarray(target_mask).save(task_dir / "target-mask.png")
        Image.fromarray(result_mask).save(task_dir / "result-object-mask.png")
        Image.fromarray(mask_preview(candidate, result_mask)).save(
            task_dir / "result-object-mask-preview.png"
        )

        masks = build_occlusion_masks(
            target_mask,
            protected_masks,
            source_task["dilation"],
            protection_radius_px=0,
            protection_underlap_px=2,
        )
        for name, value in {
            "generation-envelope.png": masks["envelope"],
            "protection-mask.png": masks["protection"],
            "generation-protection-guard.png": masks["generation_guard"],
            "effective-mask.png": masks["editable"],
        }.items():
            Image.fromarray(value).save(task_dir / name)

        pipeline_mode = source_task.get("pipeline_mode", "legacy")
        quality_report = None
        if pipeline_mode == "object_v2":
            commit, alpha, trimap = build_semantic_alpha(
                target_mask,
                result_mask,
                masks["editable"],
                cleanup_radius_px=source_task.get("cleanup_radius", 10),
                result_radius_px=2,
                edge_width_px=source_task.get("semantic_edge", 6),
            )
            result = alpha_composite_with_foreground(
                original, candidate, alpha, masks["generation_guard"]
            )
            quality_report = build_quality_report(
                original,
                result,
                alpha,
                masks["envelope"],
                masks["generation_guard"],
                result_object_mask=result_mask,
            )
            Image.fromarray(commit).save(task_dir / "commit-mask.png")
            Image.fromarray(alpha).save(task_dir / "commit-alpha.png")
            Image.fromarray(trimap).save(task_dir / "commit-trimap.png")
            (task_dir / "quality-report.json").write_text(
                json.dumps(quality_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            masks = build_occlusion_masks(
                target_mask,
                protected_masks,
                source_task["dilation"],
                protection_radius_px=0,
                protection_underlap_px=2,
                result_object_mask=result_mask,
                result_radius_px=2,
            )
            result = composite_occlusion_layers(
                original,
                candidate,
                masks["commit"],
                masks["generation_guard"],
                feather_px=source_task.get("feather", 5),
            )
            Image.fromarray(masks["commit"]).save(task_dir / "commit-mask.png")

        version_id = storage.new_id("ver")
        filename = f"{version_id}.png"
        Image.fromarray(result).save(storage.project_dir(project_id) / "versions" / filename)
        project = storage.read_project(project_id)
        project["versions"].insert(0, {
            "id": version_id,
            "filename": filename,
            "task_id": task_id,
            "operation": source_task["operation"],
            "prompt": source_task["prompt"],
            "mask_id": source_task["mask_id"],
            "protected_mask_ids": source_task.get("protected_mask_ids", []),
            "result_object_prompt": result_prompt,
            "pipeline_mode": pipeline_mode,
            "source_ref": source_ref,
            "width": int(result.shape[1]),
            "height": int(result.shape[0]),
            "created_at": storage.now_iso(),
            "reprocess_of_task": source_task_id,
            "reused_paid_generation": True,
            "result_segmentation_provider": "wavespeed-sam3",
        })
        storage.write_project(project)
        artifacts = {
            "result": f"../../versions/{filename}",
            "layered_candidate_full": "layered-candidate-full.png",
            "target_mask": "target-mask.png",
            "result_object_mask": "result-object-mask.png",
            "result_object_mask_preview": "result-object-mask-preview.png",
            "generation_envelope": "generation-envelope.png",
            "protection_mask": "protection-mask.png",
            "generation_protection_guard": "generation-protection-guard.png",
            "effective_mask": "effective-mask.png",
            "commit_mask": "commit-mask.png",
            "provider_record": {
                "provider": "reused-image2-candidate",
                "source_task_id": source_task_id,
                "result_segmentation": sam_record,
            },
        }
        if pipeline_mode == "object_v2":
            artifacts.update({
                "commit_alpha": "commit-alpha.png",
                "commit_trimap": "commit-trimap.png",
                "quality_report": "quality-report.json",
            })
        storage.update_task(
            project_id,
            task_id,
            status="completed",
            stage="SAM3 重分割与回贴完成",
            progress=100,
            version_id=version_id,
            artifacts=artifacts,
            error=None,
        )
        return {
            "source_task_id": source_task_id,
            "task_id": task_id,
            "version_id": version_id,
            "result_path": str(storage.project_dir(project_id) / "versions" / filename),
            "pipeline_mode": pipeline_mode,
            "sam3": sam_record,
            "coverage": round(coverage, 6),
            "quality_report": quality_report,
        }
    except Exception as exc:
        (task_dir / "error.txt").write_text(str(exc), encoding="utf-8")
        storage.update_task(
            project_id,
            task_id,
            status="failed",
            stage="SAM3 重分割失败；旧结果与候选图均已保留",
            progress=100,
            error=str(exc),
            artifacts={"error_log": "error.txt", "layered_candidate_full": "layered-candidate-full.png"},
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--task", action="append", required=True)
    args = parser.parse_args()
    results = [reprocess(args.project, task_id) for task_id in args.task]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
