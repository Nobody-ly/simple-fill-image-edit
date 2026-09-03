from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import threading
import uuid

from .config import settings


_lock = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def project_dir(project_id: str) -> Path:
    return settings.data_dir / "projects" / project_id


def read_project(project_id: str) -> dict[str, Any]:
    path = project_dir(project_id) / "project.json"
    if not path.is_file():
        raise FileNotFoundError(project_id)
    return json.loads(path.read_text(encoding="utf-8"))


def write_project(project: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        project["updated_at"] = now_iso()
        _atomic_json(project_dir(project["id"]) / "project.json", project)
    return project


def create_project(name: str, source_name: str, width: int, height: int) -> dict[str, Any]:
    project_id = f"ip_{uuid.uuid4().hex[:16]}"
    created = now_iso()
    project = {
        "id": project_id,
        "name": name.strip() or f"局部编辑 {created[5:16].replace('T', ' ')}",
        "source_name": source_name,
        "width": width,
        "height": height,
        "created_at": created,
        "updated_at": created,
        "active_mask_id": None,
        "masks": [],
        "versions": [],
        "tasks": [],
    }
    folder = project_dir(project_id)
    (folder / "masks").mkdir(parents=True, exist_ok=True)
    (folder / "tasks").mkdir(parents=True, exist_ok=True)
    (folder / "versions").mkdir(parents=True, exist_ok=True)
    return write_project(project)


def list_projects() -> list[dict[str, Any]]:
    root = settings.data_dir / "projects"
    root.mkdir(parents=True, exist_ok=True)
    found = []
    for path in root.glob("*/project.json"):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    found.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return found


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def create_task(project_id: str, operation: str, prompt: str, mask_id: str,
                dilation: int, feather: int = 5,
                protected_mask_ids: list[str] | None = None,
                result_object_prompt: str = "",
                pipeline_mode: str = "object_v2",
                cleanup_radius: int = 10,
                semantic_edge: int = 6,
                growth_ratio: float = 0.35) -> dict[str, Any]:
    project = read_project(project_id)
    task_id = new_id("task")
    created = now_iso()
    task = {
        "id": task_id,
        "project_id": project_id,
        "operation": operation,
        "prompt": prompt,
        "mask_id": mask_id,
        "dilation": dilation,
        "feather": feather,
        "protected_mask_ids": protected_mask_ids or [],
        "result_object_prompt": result_object_prompt.strip(),
        "pipeline_mode": pipeline_mode,
        "cleanup_radius": cleanup_radius,
        "semantic_edge": semantic_edge,
        "growth_ratio": growth_ratio,
        "status": "created",
        "stage": "等待执行",
        "progress": 0,
        "created_at": created,
        "updated_at": created,
        "error": None,
        "provider": "lama" if operation == "remove" else "image2",
        "artifacts": {},
    }
    folder = project_dir(project_id) / "tasks" / task_id
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_json(folder / "task.json", task)
    project["tasks"].insert(0, task_id)
    write_project(project)
    return task


def read_task(project_id: str, task_id: str) -> dict[str, Any]:
    path = project_dir(project_id) / "tasks" / task_id / "task.json"
    if not path.is_file():
        raise FileNotFoundError(task_id)
    return json.loads(path.read_text(encoding="utf-8"))


def update_task(project_id: str, task_id: str, **changes: Any) -> dict[str, Any]:
    with _lock:
        task = read_task(project_id, task_id)
        task.update(changes)
        task["updated_at"] = now_iso()
        _atomic_json(project_dir(project_id) / "tasks" / task_id / "task.json", task)
        project = read_project(project_id)
        write_project(project)
    return task


def public_project(project: dict[str, Any]) -> dict[str, Any]:
    result = dict(project)
    pid = project["id"]
    result["source_url"] = f"/media/projects/{pid}/source.png"
    result["masks"] = [
        {**mask, "url": f"/media/projects/{pid}/masks/{mask['id']}.png",
         "preview_url": f"/media/projects/{pid}/masks/{mask['id']}-preview.png"}
        for mask in project.get("masks", [])
    ]
    result["versions"] = [
        {**version, "url": f"/media/projects/{pid}/versions/{version['filename']}"}
        for version in project.get("versions", [])
    ]
    tasks = []
    for task_id in project.get("tasks", []):
        try:
            tasks.append(read_task(pid, task_id))
        except FileNotFoundError:
            pass
    result["tasks"] = tasks
    return result
