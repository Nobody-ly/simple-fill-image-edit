from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
import shutil

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from . import storage
from .compositor import mask_preview, read_rgb
from .config import settings
from .pipeline import SimpleFillOptions, run_simple_fill
from .sam3_wavespeed import segment as sam3_segment


app = FastAPI(title="Simple Fill Image Edit", version="1.0.0")
app.mount("/static", StaticFiles(directory=settings.root / "app" / "static"), name="static")
app.mount("/media", StaticFiles(directory=settings.data_dir), name="media")
executor = ThreadPoolExecutor(max_workers=settings.worker_count, thread_name_prefix="simple-fill")


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
    points: list[Point] = Field(default_factory=list)
    boxes: list[Box] = Field(default_factory=list)
    prompt: str = Field(default="", max_length=64)
    source_ref: str = "source"


class GenerateRequest(BaseModel):
    operation: Literal["fill"] = "fill"
    mask_id: str
    prompt: str = Field(min_length=1)
    dilation: int = Field(default=6, ge=0, le=24)
    feather: int = Field(default=3, ge=0, le=16)
    growth_ratio: float = Field(default=0.35, ge=0.0, le=1.0)
    pipeline_mode: Literal["simple_fill"] = "simple_fill"
    protected_mask_ids: list[str] = Field(default_factory=list)
    result_object_prompt: str = ""
    cleanup_radius: int = 0
    semantic_edge: int = 0


class EditDraftRequest(BaseModel):
    source_ref: str = "source"
    target_mask_id: str | None = None
    protected_mask_ids: list[str] = Field(default_factory=list)


def _project(project_id: str) -> dict:
    return storage.read_project(project_id)


def _source(project: dict, source_ref: str) -> Path:
    folder = storage.project_dir(project["id"])
    if source_ref == "source":
        return folder / "source.png"
    version = next((v for v in project.get("versions", []) if v["id"] == source_ref), None)
    if not version:
        raise FileNotFoundError(f"version not found: {source_ref}")
    return folder / "versions" / version["filename"]


def _mask(project: dict, mask_id: str) -> dict:
    item = next((m for m in project.get("masks", []) if m["id"] == mask_id), None)
    if not item:
        raise FileNotFoundError(f"mask not found: {mask_id}")
    return item


@app.get("/")
def index():
    return FileResponse(settings.root / "app" / "static" / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "simple_semantic_fill": True,
        "pipeline_default": "simple_fill",
        "wavespeed_key_ready": settings.sam3_ready,
        "image_api_ready": settings.image_ready,
        "image_model": settings.image_model,
    }


@app.get("/api/projects")
def list_projects():
    return [{
        "id": p["id"], "name": p["name"], "updated_at": p["updated_at"],
        "width": p["width"], "height": p["height"],
        "versions": len(p.get("versions", [])), "tasks": len(p.get("tasks", [])),
        "thumbnail_url": (
            f"/media/projects/{p['id']}/versions/{p['versions'][0]['filename']}"
            if p.get("versions") else f"/media/projects/{p['id']}/source.png"
        ),
    } for p in storage.list_projects()]


@app.post("/api/projects")
async def create_project(name: str = Form(""), image: UploadFile = File(...)):
    if image.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(415, "only PNG, JPEG and WebP are supported")
    raw = await image.read()
    if len(raw) > settings.max_upload_mib * 1024 * 1024:
        raise HTTPException(413, f"image exceeds {settings.max_upload_mib} MiB")
    try:
        from io import BytesIO
        with Image.open(BytesIO(raw)) as opened:
            normalized = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = normalized.size
            if width * height > settings.max_image_pixels:
                raise HTTPException(413, "image pixel count exceeds configured limit")
            project = storage.create_project(name, image.filename or "image", width, height)
            normalized.save(storage.project_dir(project["id"]) / "source.png", "PNG")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"cannot read image: {exc}") from exc
    return storage.public_project(project)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    try:
        return storage.public_project(_project(project_id))
    except FileNotFoundError as exc:
        raise HTTPException(404, "project not found") from exc


@app.post("/api/projects/{project_id}/edit-draft")
def save_edit_draft(project_id: str, request: EditDraftRequest):
    try:
        project = _project(project_id)
        project["edit_draft"] = request.model_dump()
        storage.write_project(project)
        return project["edit_draft"]
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/projects/{project_id}/segment")
def segment(project_id: str, request: SegmentRequest):
    if not request.points and not request.boxes and not request.prompt.strip():
        raise HTTPException(400, "provide a semantic label, point or box")
    try:
        project = _project(project_id)
        source = _source(project, request.source_ref)
        points = [item.model_dump() for item in request.points]
        boxes = [item.model_dump() for item in request.boxes]
        mask, provider = sam3_segment(
            source, points=points, boxes=boxes, prompt=request.prompt
        )
        mask_id = storage.new_id("mask")
        folder = storage.project_dir(project_id) / "masks"
        Image.fromarray(mask).save(folder / f"{mask_id}.png")
        Image.fromarray(mask_preview(read_rgb(source), mask)).save(
            folder / f"{mask_id}-preview.png"
        )
        record = {
            "id": mask_id, "source_ref": request.source_ref,
            "points": points, "boxes": boxes, "prompt": request.prompt,
            "coverage": round(float((mask > 0).mean()), 5),
            "created_at": storage.now_iso(), "provider": provider,
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
        raise HTTPException(502, f"SAM3 failed: {exc}") from exc


def _run_task(project_id: str, task_id: str) -> None:
    task_dir = storage.project_dir(project_id) / "tasks" / task_id
    try:
        task = storage.read_task(project_id, task_id)
        project = _project(project_id)
        mask_meta = _mask(project, task["mask_id"])
        source_ref = mask_meta.get("source_ref", "source")
        source_path = _source(project, source_ref)
        mask_path = storage.project_dir(project_id) / "masks" / f"{task['mask_id']}.png"

        def progress(stage: str, value: int):
            storage.update_task(
                project_id, task_id, status="generating", stage=stage, progress=value
            )

        result_path, report = run_simple_fill(
            source_path,
            mask_path,
            task["prompt"],
            task_dir,
            options=SimpleFillOptions(
                dilation_px=task.get("dilation", 6),
                growth_ratio=task.get("growth_ratio", 0.35),
                feather_px=task.get("feather", 3),
            ),
            progress=progress,
        )
        version_id = storage.new_id("ver")
        filename = f"{version_id}.png"
        shutil.copy2(result_path, storage.project_dir(project_id) / "versions" / filename)
        project = _project(project_id)
        project["versions"].insert(0, {
            "id": version_id, "filename": filename, "task_id": task_id,
            "operation": "fill", "prompt": task["prompt"],
            "mask_id": task["mask_id"], "source_ref": source_ref,
            "width": project["width"], "height": project["height"],
            "created_at": storage.now_iso(), "pipeline_mode": "simple_fill",
        })
        storage.write_project(project)
        storage.update_task(
            project_id, task_id, status="completed", stage="completed", progress=100,
            version_id=version_id, error=None,
            artifacts={
                "result": f"../../versions/{filename}",
                "target_mask": "target-mask.png", "effective_mask": "edit-mask.png",
                "inpaint_anything_crop": "inpaint-anything-crop.png",
                "provider_original": "image-edit-provider-original.png",
                "layered_candidate_full": "candidate-full.png",
                "run_record": "run.json", "provider_record": report["provider"],
            },
        )
    except Exception as exc:
        storage.update_task(
            project_id, task_id, status="failed", stage="failed; inputs retained",
            progress=100, error=str(exc)[:2000]
        )


@app.post("/api/projects/{project_id}/generate")
def generate(project_id: str, request: GenerateRequest):
    try:
        project = _project(project_id)
        _mask(project, request.mask_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    task = storage.create_task(
        project_id, "fill", request.prompt.strip(), request.mask_id,
        request.dilation, request.feather, [], "", "simple_fill", 0, 0,
        request.growth_ratio,
    )
    executor.submit(_run_task, project_id, task["id"])
    return task


@app.post("/api/projects/{project_id}/tasks/{task_id}/retry")
def retry(project_id: str, task_id: str):
    try:
        previous = storage.read_task(project_id, task_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "task not found") from exc
    if previous["status"] not in {"failed", "completed"}:
        raise HTTPException(409, "task is still running")
    task = storage.create_task(
        project_id, "fill", previous["prompt"], previous["mask_id"],
        previous.get("dilation", 6), previous.get("feather", 3), [], "",
        "simple_fill", 0, 0, previous.get("growth_ratio", 0.35),
    )
    task = storage.update_task(project_id, task["id"], retry_of=task_id)
    executor.submit(_run_task, project_id, task["id"])
    return task


@app.get("/api/projects/{project_id}/tasks/{task_id}")
def get_task(project_id: str, task_id: str):
    try:
        return storage.read_task(project_id, task_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "task not found") from exc


@app.get("/api/projects/{project_id}/versions/{version_id}/download")
def download_version(project_id: str, version_id: str):
    try:
        project = _project(project_id)
        version = next(v for v in project["versions"] if v["id"] == version_id)
        path = storage.project_dir(project_id) / "versions" / version["filename"]
    except (FileNotFoundError, StopIteration) as exc:
        raise HTTPException(404, "version not found") from exc
    return FileResponse(path, media_type="image/png", filename=f"{project['name']}-{version_id}.png")
