from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import ModuleType
import importlib.util

import numpy as np

from .config import settings


@lru_cache(maxsize=1)
def _upstream_mask_processing() -> ModuleType:
    """Load the upstream implementation without copying or changing it."""
    source = settings.upstream_dir / "utils" / "mask_processing.py"
    spec = importlib.util.spec_from_file_location(
        "catsco_upstream_inpaint_anything_mask_processing", source,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 Inpaint Anything 上游模块：{source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_fill_crop(image: np.ndarray, mask: np.ndarray,
                      crop_size: int = 512) -> tuple[np.ndarray, np.ndarray]:
    module = _upstream_mask_processing()
    return module.crop_for_filling_pre(image, mask, crop_size=crop_size)


def restore_fill_crop(image: np.ndarray, mask: np.ndarray,
                      generated_crop: np.ndarray,
                      crop_size: int = 512) -> np.ndarray:
    module = _upstream_mask_processing()
    return module.crop_for_filling_post(
        image, mask, generated_crop, crop_size=crop_size,
    )


def upstream_source_path() -> Path:
    return settings.upstream_dir / "utils" / "mask_processing.py"
