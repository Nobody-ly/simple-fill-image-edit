from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
import json
import threading

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from . import storage
from .compositor import (
    build_occlusion_masks, build_simple_fill_mask, composite, composite_occlusion_layers, expand_mask,
    feathered_composite, image2_mask_guide,
    mask_preview, read_mask, read_rgb,
)
from .config import settings
from .image2_remote import run_image2
from .image2_openai import run_masked_image2
from .image2_catsco_mask_remote import run_catsco_masked_image2
from .inpaint_anything_pipeline import prepare_fill_crop, restore_fill_crop
from .lama_backend import inpaint as lama_inpaint, validate_installation
from .object_edit_v2 import (
    alpha_composite_with_foreground,
    build_clean_plate_mask,
    build_quality_report,
    build_semantic_alpha,
    segment_changed_object,
)
from .sam3_wavespeed import segment as sam3_segment


app = FastAPI(title="CatsCo Semantic Fill", version="0.1.0-experimental")
app.mount("/static", StaticFiles(directory=settings.root / "app" / "static"), name="static")
app.mount("/media", StaticFiles(directory=settings.data_dir), name="media")

executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="inpaint-task")
lama_run_lock = threading.Lock()


class Point(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    label: Literal[0, 1] = 1


class Box(BaseModel):
    x_min: int
    y_min: int
    x_max: int
    y_max: int


class SegmentRequest(BaseModel):
    points: list[Point] = []
    boxes: list[Box] = []
    prompt: str = Field(default="", max_length=32)
    source_ref: str = "source"


class GenerateRequest(BaseModel):
    operation: Literal["remove", "fill", "replace_background"]
    mask_id: str
    prompt: str = ""
    dilation: int = Field(default=6, ge=0, le=24)
    feather: int = Field(default=3, ge=0, le=8)
    protected_mask_ids: list[str] = Field(default_factory=list)
    result_object_prompt: str = Field(default="", max_length=32)
    pipeline_mode: Literal["legacy", "object_v2", "simple_fill"] = "simple_fill"
    cleanup_radius: int = Field(default=10, ge=0, le=24)
    semantic_edge: int = Field(default=6, ge=0, le=16)
    growth_ratio: float = Field(default=0.35, ge=0.0, le=1.0)


class EditDraftRequest(BaseModel):
    source_ref: str = "source"
    target_mask_id: str | None = None
    protected_mask_ids: list[str] = Field(default_factory=list)


def _resolve_source(project: dict, source_ref: str) -> Path:
    folder = storage.project_dir(project["id"])
    if source_ref == "source":
        return folder / "source.png"
    version = next((item for item in project.get("versions", []) if item["id"] == source_ref), None)
    if version is None:
        raise FileNotFoundError(f"不存在的版本：{source_ref}")
    return folder / "versions" / version["filename"]


def _mask_record(project: dict, mask_id: str) -> dict:
    record = next((item for item in project.get("masks", []) if item["id"] == mask_id), None)
    if record is None:
        raise FileNotFoundError(f"不存在的蒙版：{mask_id}")
    return record


@app.get("/")
def index():
    return FileResponse(settings.root / "app" / "static" / "index.html")


@app.get("/api/health")
def health():
    info = validate_installation()
    info.update({
        "ok": True,
        "experimental_object_edit_v2": True,
        "simple_semantic_fill": True,
        "pipeline_default": "simple_fill",
        "wavespeed_key_ready": bool(settings.wavespeed_key_file.is_file()),
        "image2_ssh_key_ready": settings.ssh_key.is_file(),
        "image2_native_mask_ready": settings.masked_image2_key_ready or settings.ssh_key.is_file(),
        "image2_native_mask_route": (
            "direct-openai" if settings.masked_image2_key_ready else
            "catsco-gateway" if settings.ssh_key.is_file() else "unavailable"
        ),
        "image2_native_mask_model": settings.masked_image2_model,
        "upstream_ready": settings.upstream_dir.is_dir(),
    })
    return info


@app.get("/api/projects")
def projects():
    return [{
        "id": item["id"], "name": item["name"], "updated_at": item["updated_at"],
        "width": item["width"], "height": item["height"],
        "versions": len(item.get("versions", [])), "tasks": len(item.get("tasks", [])),
        "thumbnail_url": (
            f"/media/projects/{item['id']}/versions/{item['versions'][0]['filename']}"
            if item.get("versions") else f"/media/projects/{item['id']}/source.png"
        ),
    } for item in storage.list_projects()]


@app.post("/api/projects")
async def create_project(name: str = Form(""), image: UploadFile = File(...)):
    if image.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(415, "仅支持 PNG、JPEG、WebP")
    raw = await image.read()
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(413, "单图超过 WaveSpeed 的 200 MiB 接口上限")
    try:
        from io import BytesIO
        with Image.open(BytesIO(raw)) as opened:
            normalized = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = normalized.size
            if width * height > 120_000_000:
                raise HTTPException(413, "图片像素总量超过 1.2 亿，浏览器无法安全处理")
            project = storage.create_project(name, image.filename or "image", width, height)
            normalized.save(storage.project_dir(project["id"]) / "source.png", format="PNG")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"无法读取图片：{exc}") from exc
    return storage.public_project(project)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    try:
        return storage.public_project(storage.read_project(project_id))
    except FileNotFoundError as exc:
        raise HTTPException(404, "项目不存在") from exc


@app.post("/api/projects/{project_id}/edit-draft")
def save_edit_draft(project_id: str, request: EditDraftRequest):
    try:
        project = storage.read_project(project_id)
        mask_ids = ([request.target_mask_id] if request.target_mask_id else []) + request.protected_mask_ids
        for mask_id in mask_ids:
            record = _mask_record(project, mask_id)
            if record.get("source_ref", "source") != request.source_ref:
                raise HTTPException(400, "编辑草稿中的蒙版不属于当前底图版本")
        project["edit_draft"] = request.model_dump()
        storage.write_project(project)
        return project["edit_draft"]
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/projects/{project_id}/segment")
def segment(project_id: str, request: SegmentRequest):
    if not request.points and not request.boxes and not request.prompt.strip():
        raise HTTPException(400, "请至少点一下目标、画框或输入目标名称")
    try:
        project = storage.read_project(project_id)
        source = _resolve_source(project, request.source_ref)
        points = [item.model_dump() for item in request.points]
        boxes = [item.model_dump() for item in request.boxes]
        mask, provider = sam3_segment(
            source, points=points, boxes=boxes, prompt=request.prompt,
        )
        mask_id = storage.new_id("mask")
        folder = storage.project_dir(project_id) / "masks"
        Image.fromarray(mask).save(folder / f"{mask_id}.png")
        preview = mask_preview(read_rgb(source), mask)
        Image.fromarray(preview).save(folder / f"{mask_id}-preview.png")
        record = {
            "id": mask_id,
            "source_ref": request.source_ref,
            "points": points,
            "boxes": boxes,
            "prompt": request.prompt,
            "coverage": round(float((mask > 0).mean()), 5),
            "created_at": storage.now_iso(),
            "provider": provider,
        }
        project["masks"].insert(0, record)
        project["active_mask_id"] = mask_id
        storage.write_project(project)
        return {
            **record,
            "url": f"/media/projects/{project_id}/masks/{mask_id}.png",
            "preview_url": f"/media/projects/{project_id}/masks/{mask_id}-preview.png",
        }
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"SAM3 分割失败：{exc}") from exc


def _set_task(project_id: str, task_id: str, stage: str, progress: int, **extra):
    return storage.update_task(project_id, task_id, stage=stage, progress=progress, **extra)


def _run_task(project_id: str, task_id: str) -> None:
    task_dir = storage.project_dir(project_id) / "tasks" / task_id
    try:
        task = storage.read_task(project_id, task_id)
        project = storage.read_project(project_id)
        mask_meta = _mask_record(project, task["mask_id"])
        source = _resolve_source(project, mask_meta.get("source_ref", "source"))
        original = read_rgb(source)
        mask = read_mask(
            storage.project_dir(project_id) / "masks" / f"{task['mask_id']}.png",
            (original.shape[1], original.shape[0]),
        )
        protection_masks: list[np.ndarray] = []
        for protected_id in task.get("protected_mask_ids", []):
            protected_meta = _mask_record(project, protected_id)
            if protected_meta.get("source_ref", "source") != mask_meta.get("source_ref", "source"):
                raise RuntimeError("目标蒙版与前景保护蒙版不属于同一底图版本")
            protection_masks.append(read_mask(
                storage.project_dir(project_id) / "masks" / f"{protected_id}.png",
                (original.shape[1], original.shape[0]),
            ))
        if task.get("pipeline_mode") == "simple_fill" and task["operation"] == "fill":
            simple_mask, simple_mask_record = build_simple_fill_mask(
                mask,
                cleanup_radius_px=task.get("dilation", 6),
                growth_ratio=task.get("growth_ratio", 0.35),
            )
            layered_masks = {
                "envelope": simple_mask,
                "protection": np.zeros_like(simple_mask),
                "generation_guard": np.zeros_like(simple_mask),
                "editable": simple_mask,
                "commit": simple_mask,
            }
        else:
            simple_mask_record = None
            layered_masks = build_occlusion_masks(
                mask, protection_masks, task["dilation"],
                protection_radius_px=0, protection_underlap_px=2,
            )
        effective_mask = layered_masks["editable"]
        if not np.any(effective_mask):
            raise RuntimeError("前景保护区域覆盖了全部生成范围，请减少保护区域或扩大生成范围")
        Image.fromarray(mask).save(task_dir / "target-mask.png")
        Image.fromarray(layered_masks["envelope"]).save(task_dir / "generation-envelope.png")
        Image.fromarray(layered_masks["protection"]).save(task_dir / "protection-mask.png")
        Image.fromarray(layered_masks["generation_guard"]).save(
            task_dir / "generation-protection-guard.png"
        )
        effective_path = task_dir / "effective-mask.png"
        Image.fromarray(effective_mask).save(effective_path)
        _set_task(project_id, task_id, "输入与蒙版已锁定", 18,
                  status="generating", artifacts={
                      "target_mask": "target-mask.png",
                      "generation_envelope": "generation-envelope.png",
                      "protection_mask": "protection-mask.png",
                      "generation_protection_guard": "generation-protection-guard.png",
                      "effective_mask": "effective-mask.png",
                      "simple_mask_record": simple_mask_record,
                  })

        clean_plate = original
        clean_plate_mask = None
        if task["operation"] == "fill" and task.get("pipeline_mode") == "object_v2":
            clean_plate_path = task_dir / "clean-plate.png"
            clean_plate_mask_path = task_dir / "clean-plate-mask.png"
            if clean_plate_path.is_file() and clean_plate_mask_path.is_file():
                _set_task(project_id, task_id, "正在复用同一任务的干净底板", 23)
                clean_plate = read_rgb(clean_plate_path)
                clean_plate_mask = read_mask(
                    clean_plate_mask_path,
                    (original.shape[1], original.shape[0]),
                )
            else:
                _set_task(project_id, task_id, "Big-LaMa 正在先清除旧对象", 23)
                clean_plate_mask = build_clean_plate_mask(
                    mask,
                    layered_masks["generation_guard"],
                    cleanup_radius_px=task.get("cleanup_radius", 10),
                )
                if not np.any(clean_plate_mask):
                    raise RuntimeError("旧对象清理区域为空，请检查目标与前景保护蒙版")
                Image.fromarray(clean_plate_mask).save(clean_plate_mask_path)
                with lama_run_lock:
                    clean_plate = lama_inpaint(original, clean_plate_mask)
                # Big-LaMa is allowed to infer globally internally, but the V2
                # contract only commits pixels explicitly approved for cleanup.
                clean_plate[clean_plate_mask == 0] = original[clean_plate_mask == 0]
                Image.fromarray(clean_plate).save(clean_plate_path)
            current = storage.read_task(project_id, task_id)
            artifacts = current.get("artifacts", {})
            artifacts.update({
                "clean_plate_mask": "clean-plate-mask.png",
                "clean_plate": "clean-plate.png",
            })
            _set_task(project_id, task_id, "旧对象已清除，准备生成新对象", 28,
                      artifacts=artifacts)

        if task["operation"] == "remove":
            _set_task(project_id, task_id, "Big-LaMa 正在 GPU 推理", 42)
            with lama_run_lock:
                generated = lama_inpaint(original, effective_mask)
            provider_original = task_dir / "lama-provider-original.png"
            Image.fromarray(generated).save(provider_original)
            provider_artifact_name = provider_original.name
            result = generated
            provider_record = {"provider": "lama", "device": "cuda", "full_model": True}
        elif task["operation"] == "fill":
            _set_task(project_id, task_id, "裁切无旧对象的局部生成窗口", 31)
            crop_image, crop_mask = prepare_fill_crop(
                clean_plate, effective_mask, crop_size=512,
            )
            crop_source_path = task_dir / "inpaint-anything-crop.png"
            Image.fromarray(crop_image).save(crop_source_path)
            def progress(stage: str, value: int):
                _set_task(project_id, task_id, stage, value)

            if settings.masked_image2_key_ready:
                provider_path, provider_record = run_masked_image2(
                    crop_source_path,
                    crop_mask,
                    task["prompt"],
                    task_dir,
                    progress,
                )
            elif settings.ssh_key.is_file():
                provider_path, provider_record = run_catsco_masked_image2(
                    task_id,
                    crop_source_path,
                    crop_mask,
                    task["prompt"],
                    task_dir,
                    progress,
                )
            else:
                crop_guide = image2_mask_guide(crop_mask)
                guide_path = task_dir / "inpaint-anything-mask-guide.png"
                Image.fromarray(crop_guide).save(guide_path)
                provider_path, provider_record = run_image2(
                    task_id, crop_source_path, guide_path,
                    crop_image.shape[1], crop_image.shape[0],
                    task["operation"], task["prompt"], task_dir, progress,
                    inpaint_anything_crop=True,
                )
            provider_artifact_name = provider_path.name
            generated_crop = read_rgb(provider_path)
            if generated_crop.shape[:2] != crop_image.shape[:2]:
                generated_crop = cv2.resize(
                    generated_crop, (crop_image.shape[1], crop_image.shape[0]),
                    interpolation=cv2.INTER_LANCZOS4,
                )
            normalized_crop = task_dir / "inpaint-anything-generated-crop.png"
            Image.fromarray(generated_crop).save(normalized_crop)
            _set_task(project_id, task_id, "按 Inpaint Anything 原版蒙版回填", 86)
            candidate_full = restore_fill_crop(
                original.copy(), effective_mask.copy(), generated_crop,
                crop_size=512,
            )
            candidate_path = task_dir / "layered-candidate-full.png"
            Image.fromarray(candidate_full).save(candidate_path)
            commit_mask = effective_mask
            result_object_mask = None
            result_segmentation_record = None
            result_object_prompt = task.get("result_object_prompt", "").strip()
            native_mask_direct = task.get("pipeline_mode") == "simple_fill" or provider_record.get("provider") in {
                "gpt-image-2-native-mask",
                "catsco-gpt-image-2-native-mask",
            }
            if result_object_prompt and not native_mask_direct:
                cached_result_mask = task_dir / "result-object-mask.png"
                if cached_result_mask.is_file():
                    _set_task(project_id, task_id, "正在复用已验证的新对象蒙版", 88)
                    result_object_mask = read_mask(
                        cached_result_mask,
                        (candidate_full.shape[1], candidate_full.shape[0]),
                    )
                    result_segmentation_record = {
                        "provider": "cached-sam3-mask",
                        "resumed_same_task": True,
                    }
                else:
                    _set_task(project_id, task_id, "SAM3 正在识别新生成对象", 88)
                    try:
                        result_object_mask, result_segmentation_record = sam3_segment(
                            candidate_path, points=[], boxes=[], prompt=result_object_prompt,
                        )
                    except Exception as segmentation_error:
                        _set_task(
                            project_id, task_id,
                            "SAM3 不可用，正在以本地变化区域完成回贴", 89,
                        )
                        result_object_mask, result_segmentation_record = segment_changed_object(
                            clean_plate if task.get("pipeline_mode") == "object_v2" else original,
                            candidate_full,
                            effective_mask,
                            mask,
                        )
                        result_segmentation_record["fallback_reason"] = str(
                            segmentation_error
                        )[:500]
                result_coverage = float((result_object_mask > 0).mean())
                if result_coverage <= 0 or result_coverage > 0.65:
                    raise RuntimeError(
                        f"新对象二次分割结果不可信（覆盖率 {result_coverage:.1%}），"
                        "候选图已保留，可修改识别词后恢复同一 Image2 结果"
                    )
                Image.fromarray(result_object_mask).save(task_dir / "result-object-mask.png")
                Image.fromarray(mask_preview(candidate_full, result_object_mask)).save(
                    task_dir / "result-object-mask-preview.png"
                )

            if task.get("pipeline_mode") == "object_v2":
                _set_task(project_id, task_id, "正在生成语义 Trimap 与软边 Alpha", 91)
                if native_mask_direct:
                    # The IA crop has already been restored through the exact
                    # approved edit mask.  A second semantic segmentation must
                    # not shrink that region: doing so reintroduces pieces of
                    # the old object/clean plate around hands and contact edges.
                    commit_mask = effective_mask.copy()
                    commit_alpha = effective_mask.copy()
                    trimap = effective_mask.copy()
                elif result_object_mask is not None:
                    commit_mask, commit_alpha, trimap = build_semantic_alpha(
                        mask,
                        result_object_mask,
                        effective_mask,
                        cleanup_radius_px=task.get("cleanup_radius", 10),
                        result_radius_px=2,
                        edge_width_px=task.get("semantic_edge", 6),
                    )
                else:
                    # Without a short result-object label we cannot know the
                    # new semantic outline. Keep the full approved generation
                    # window, but still use the V2 inside-edge alpha.
                    commit_mask, commit_alpha, trimap = build_semantic_alpha(
                        effective_mask,
                        None,
                        effective_mask,
                        cleanup_radius_px=0,
                        result_radius_px=0,
                        edge_width_px=task.get("semantic_edge", 6),
                    )
                Image.fromarray(commit_mask).save(task_dir / "commit-mask.png")
                Image.fromarray(commit_alpha).save(task_dir / "commit-alpha.png")
                Image.fromarray(trimap).save(task_dir / "commit-trimap.png")
                result = alpha_composite_with_foreground(
                    original,
                    candidate_full,
                    commit_alpha,
                    layered_masks["generation_guard"],
                )
                quality_report = build_quality_report(
                    original,
                    result,
                    commit_alpha,
                    layered_masks["envelope"],
                    layered_masks["generation_guard"],
                    result_object_mask=result_object_mask,
                )
                (task_dir / "quality-report.json").write_text(
                    json.dumps(quality_report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if not quality_report["safety_passed"]:
                    raise RuntimeError(
                        "V2 合成安全检查未通过；候选与中间文件已保留，未登记为正式版本"
                    )
            else:
                if native_mask_direct:
                    commit_mask = effective_mask.copy()
                    Image.fromarray(commit_mask).save(task_dir / "commit-mask.png")
                    if task.get("pipeline_mode") == "simple_fill":
                        result = feathered_composite(
                            original,
                            candidate_full,
                            commit_mask,
                            feather_px=task.get("feather", 3),
                            operation="fill",
                        )
                        quality_report = build_quality_report(
                            original,
                            result,
                            commit_mask,
                            layered_masks["envelope"],
                            layered_masks["generation_guard"],
                        )
                        (task_dir / "quality-report.json").write_text(
                            json.dumps(quality_report, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        if not quality_report["safety_passed"]:
                            raise RuntimeError("Simple Fill 合成安全检查未通过")
                    else:
                        result = candidate_full
                else:
                    layered_masks = build_occlusion_masks(
                        mask, protection_masks, task["dilation"],
                        protection_radius_px=0,
                        protection_underlap_px=2,
                        result_object_mask=result_object_mask,
                        result_radius_px=2,
                    )
                    commit_mask = layered_masks["commit"]
                    Image.fromarray(commit_mask).save(task_dir / "commit-mask.png")
                    result = composite_occlusion_layers(
                        original, candidate_full, commit_mask,
                        layered_masks["generation_guard"],
                        feather_px=task.get("feather", 5),
                    )
            provider_record["inpaint_anything"] = {
                "mode": task.get("pipeline_mode", "legacy"),
                "crop_size": 512,
                "native_mask": (
                    settings.masked_image2_key_ready or settings.ssh_key.is_file()
                ),
                "upstream_preprocess": "utils.mask_processing.crop_for_filling_pre",
                "upstream_postprocess": "utils.mask_processing.crop_for_filling_post",
                "mask_expansion_radius_px": task["dilation"],
                "blend_feather_px": task.get("feather", 5),
                "clean_plate_first": task.get("pipeline_mode") == "object_v2",
                "cleanup_radius_px": task.get("cleanup_radius", 10),
                "semantic_edge_px": task.get("semantic_edge", 6),
                "protected_mask_ids": task.get("protected_mask_ids", []),
                "foreground_underlap_px": 2,
                "result_object_prompt": result_object_prompt or None,
                "result_segmentation": result_segmentation_record,
                "composition_policy": (
                    "native-mask-full-candidate"
                    if native_mask_direct else "semantic-result-object"
                ),
                "simple_fill_mask": simple_mask_record,
                "layer_order": [
                    "original", "generated_object", "generated_contact_edge",
                    "protected_foreground_interior",
                ],
            }
        else:
            guide = image2_mask_guide(effective_mask)
            guide_path = task_dir / "mask-guide.png"
            Image.fromarray(guide).save(guide_path)

            def progress(stage: str, value: int):
                _set_task(project_id, task_id, stage, value)

            provider_path, provider_record = run_image2(
                task_id, source, guide_path, original.shape[1], original.shape[0],
                task["operation"], task["prompt"], task_dir, progress,
            )
            provider_artifact_name = provider_path.name
            generated = read_rgb(provider_path)
            result = feathered_composite(
                original, generated, effective_mask,
                feather_px=task.get("feather", 5), operation=task["operation"],
            )

        _set_task(project_id, task_id, "正在保存可回溯版本", 92)
        version_id = storage.new_id("ver")
        filename = f"{version_id}.png"
        Image.fromarray(result).save(storage.project_dir(project_id) / "versions" / filename)
        project = storage.read_project(project_id)
        version = {
            "id": version_id,
            "filename": filename,
            "task_id": task_id,
            "operation": task["operation"],
            "prompt": task["prompt"],
            "mask_id": task["mask_id"],
            "protected_mask_ids": task.get("protected_mask_ids", []),
            "result_object_prompt": task.get("result_object_prompt", ""),
            "pipeline_mode": task.get("pipeline_mode", "legacy"),
            "source_ref": mask_meta.get("source_ref", "source"),
            "width": int(result.shape[1]),
            "height": int(result.shape[0]),
            "created_at": storage.now_iso(),
        }
        project["versions"].insert(0, version)
        storage.write_project(project)
        artifacts = storage.read_task(project_id, task_id).get("artifacts", {})
        artifacts.update({
            "result": f"../../versions/{filename}",
            "provider_original": provider_artifact_name,
            "provider_record": provider_record,
        })
        if task["operation"] == "fill":
            artifacts.update({
                "inpaint_anything_crop": "inpaint-anything-crop.png",
                "inpaint_anything_generated_crop": "inpaint-anything-generated-crop.png",
                "layered_candidate_full": "layered-candidate-full.png",
                "commit_mask": "commit-mask.png",
            })
            if (task_dir / "clean-plate.png").is_file():
                artifacts["clean_plate"] = "clean-plate.png"
                artifacts["clean_plate_mask"] = "clean-plate-mask.png"
            if (task_dir / "commit-alpha.png").is_file():
                artifacts["commit_alpha"] = "commit-alpha.png"
                artifacts["commit_trimap"] = "commit-trimap.png"
            if (task_dir / "quality-report.json").is_file():
                artifacts["quality_report"] = "quality-report.json"
            if (task_dir / "result-object-mask.png").is_file():
                artifacts["result_object_mask"] = "result-object-mask.png"
                artifacts["result_object_mask_preview"] = "result-object-mask-preview.png"
            if (task_dir / "inpaint-anything-mask-guide.png").is_file():
                artifacts["inpaint_anything_mask_guide"] = "inpaint-anything-mask-guide.png"
            if (task_dir / "image2-native-alpha-mask.png").is_file():
                artifacts["image2_native_alpha_mask"] = "image2-native-alpha-mask.png"
        _set_task(project_id, task_id, "完成", 100, status="completed",
                  version_id=version_id, artifacts=artifacts, error=None)
    except Exception as exc:
        error = str(exc)
        (task_dir / "error.txt").write_text(error, encoding="utf-8")
        try:
            task = storage.read_task(project_id, task_id)
            artifacts = task.get("artifacts", {})
            artifacts["error_log"] = "error.txt"
            if (task_dir / "image2-provider-original.png").is_file():
                artifacts["provider_original"] = "image2-provider-original.png"
            if (task_dir / "image2-native-mask-provider-original.png").is_file():
                artifacts["provider_original"] = "image2-native-mask-provider-original.png"
            if (task_dir / "image2-native-alpha-mask.png").is_file():
                artifacts["image2_native_alpha_mask"] = "image2-native-alpha-mask.png"
            if (task_dir / "layered-candidate-full.png").is_file():
                artifacts["layered_candidate_full"] = "layered-candidate-full.png"
            if (task_dir / "result-object-mask.png").is_file():
                artifacts["result_object_mask"] = "result-object-mask.png"
            if (task_dir / "result-object-mask-preview.png").is_file():
                artifacts["result_object_mask_preview"] = "result-object-mask-preview.png"
            if (task_dir / "commit-mask.png").is_file():
                artifacts["commit_mask"] = "commit-mask.png"
            if (task_dir / "clean-plate.png").is_file():
                artifacts["clean_plate"] = "clean-plate.png"
                artifacts["clean_plate_mask"] = "clean-plate-mask.png"
            if (task_dir / "commit-alpha.png").is_file():
                artifacts["commit_alpha"] = "commit-alpha.png"
                artifacts["commit_trimap"] = "commit-trimap.png"
            if (task_dir / "quality-report.json").is_file():
                artifacts["quality_report"] = "quality-report.json"
            _set_task(project_id, task_id, "失败；输入、蒙版和已返回文件均已保留", 100,
                      status="failed", error=error, artifacts=artifacts)
        except Exception:
            pass


@app.post("/api/projects/{project_id}/generate")
def generate(project_id: str, request: GenerateRequest):
    try:
        project = storage.read_project(project_id)
        target_meta = _mask_record(project, request.mask_id)
        for protected_id in request.protected_mask_ids:
            protected_meta = _mask_record(project, protected_id)
            if protected_meta.get("source_ref", "source") != target_meta.get("source_ref", "source"):
                raise HTTPException(400, "目标与前景保护必须来自同一底图版本")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if request.operation != "remove" and not request.prompt.strip():
        raise HTTPException(400, "区域重绘或换背景需要一句生成要求")
    task = storage.create_task(
        project_id, request.operation, request.prompt.strip(),
        request.mask_id, request.dilation,
        request.feather,
        request.protected_mask_ids,
        request.result_object_prompt,
        request.pipeline_mode,
        request.cleanup_radius,
        request.semantic_edge,
        request.growth_ratio,
    )
    executor.submit(_run_task, project_id, task["id"])
    return task


@app.post("/api/projects/{project_id}/tasks/{task_id}/retry")
def retry(project_id: str, task_id: str):
    try:
        previous = storage.read_task(project_id, task_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "任务不存在") from exc
    if previous["status"] not in {"failed", "completed"}:
        raise HTTPException(409, "任务仍在运行，不能重复提交")
    task = storage.create_task(
        project_id, previous["operation"], previous.get("prompt", ""),
        previous["mask_id"], previous.get("dilation", 40),
        previous.get("feather", 5),
        previous.get("protected_mask_ids", []),
        previous.get("result_object_prompt", ""),
        previous.get("pipeline_mode", "legacy"),
        previous.get("cleanup_radius", 10),
        previous.get("semantic_edge", 6),
        previous.get("growth_ratio", 0.35),
    )
    task = storage.update_task(project_id, task["id"], retry_of=task_id)
    executor.submit(_run_task, project_id, task["id"])
    return task


@app.post("/api/projects/{project_id}/tasks/{task_id}/resume")
def resume(project_id: str, task_id: str):
    try:
        task = storage.read_task(project_id, task_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "任务不存在") from exc
    if task.get("provider") != "image2":
        raise HTTPException(400, "只有 Image2 任务支持恢复远程结果")
    if task.get("status") != "failed":
        raise HTTPException(409, "只有未完成的 Image2 任务可以恢复")
    task = storage.update_task(
        project_id, task_id,
        status="created", stage="正在恢复同一远程任务", progress=10, error=None,
    )
    executor.submit(_run_task, project_id, task_id)
    return task


@app.get("/api/projects/{project_id}/tasks/{task_id}")
def get_task(project_id: str, task_id: str):
    try:
        return storage.read_task(project_id, task_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "任务不存在") from exc


@app.get("/api/projects/{project_id}/versions/{version_id}/download")
def download_version(project_id: str, version_id: str):
    try:
        project = storage.read_project(project_id)
        version = next(item for item in project["versions"] if item["id"] == version_id)
        path = storage.project_dir(project_id) / "versions" / version["filename"]
    except (FileNotFoundError, StopIteration) as exc:
        raise HTTPException(404, "版本不存在") from exc
    return FileResponse(path, media_type="image/png", filename=f"{project['name']}-{version_id}.png")
