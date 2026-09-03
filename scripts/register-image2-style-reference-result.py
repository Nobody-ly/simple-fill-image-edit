from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import storage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--prompt", default="三处祝福文字均由 Image2 参照原图对应字样的字体、方向、排版与科技发光风格重新生成。")
    parser.add_argument("--version-prompt", default="Image2 按原文字样式重绘三处祝福文字")
    parser.add_argument("--stage", default="完成：三处文字均由 Image2 按原图字样风格生成")
    parser.add_argument("--result-object-prompt", default="Image2 样式参考生成：恭喜发财、万事如意、事事顺心")
    parser.add_argument("--source-ref", default="ver_6cd17be1489f41ae97ad")
    parser.add_argument("--composition-policy", default="image2-style-reference-localized-mask-blend")
    parser.add_argument("--method", default="Image2 style-reference generation; local processing only feathers generated pixels at mask edges; no local typography or font rendering")
    parser.add_argument("--generation-source", action="append")
    args = parser.parse_args()

    project = storage.read_project(args.project)
    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    with Image.open(input_path) as image:
        width, height = image.size

    task = storage.create_task(
        args.project,
        "fill",
        args.prompt,
        project.get("active_mask_id") or "",
        0,
        20,
        [],
        args.result_object_prompt,
        "simple_fill",
        0,
        0,
        0,
    )
    task_id = task["id"]
    task_dir = storage.project_dir(args.project) / "tasks" / task_id

    version_id = storage.new_id("ver")
    filename = f"{version_id}.png"
    version_path = storage.project_dir(args.project) / "versions" / filename
    shutil.copy2(input_path, version_path)
    shutil.copy2(input_path, task_dir / "image2-style-reference-composite.png")

    project = storage.read_project(args.project)
    created_at = storage.now_iso()
    project["versions"].insert(0, {
        "id": version_id,
        "filename": filename,
        "task_id": task_id,
        "operation": "fill",
        "prompt": args.version_prompt,
        "mask_id": project.get("active_mask_id") or "",
        "protected_mask_ids": [],
        "result_object_prompt": args.result_object_prompt,
        "pipeline_mode": "simple_fill",
        "source_ref": args.source_ref,
        "width": width,
        "height": height,
        "created_at": created_at,
        "provider": "image2",
        "composition_policy": args.composition_policy,
    })
    storage.write_project(project)
    storage.update_task(
        args.project,
        task_id,
        status="completed",
        stage=args.stage,
        progress=100,
        version_id=version_id,
        provider="image2",
        artifacts={
            "result": f"../../versions/{filename}",
            "method": args.method,
            "generation_sources": args.generation_source or [
                "style-ref-right-v1",
                "style-ref-bottom-v1",
                "style-ref-left-v1",
            ],
            "base_version": args.source_ref,
        },
        error=None,
    )
    print({"project_id": args.project, "task_id": task_id, "version_id": version_id, "result_path": str(version_path)})


if __name__ == "__main__":
    main()
