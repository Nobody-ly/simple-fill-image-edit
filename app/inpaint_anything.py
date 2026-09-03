from __future__ import annotations

import importlib.util
from functools import lru_cache
from types import ModuleType

import numpy as np

from .config import settings


@lru_cache(maxsize=1)
def _mask_processing() -> ModuleType:
    source = settings.root / "third_party" / "inpaint_anything_mask_processing.py"
    spec = importlib.util.spec_from_file_location("simple_fill_ia_mask_processing", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load bundled Inpaint Anything mask processing: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_crop(image: np.ndarray, mask: np.ndarray, crop_size: int = 512):
    return _mask_processing().crop_for_filling_pre(image, mask, crop_size=crop_size)


def restore_crop(image: np.ndarray, mask: np.ndarray, generated: np.ndarray, crop_size: int = 512):
    return _mask_processing().crop_for_filling_post(
        image, mask, generated, crop_size=crop_size
    )
