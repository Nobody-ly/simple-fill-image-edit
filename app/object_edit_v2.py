from __future__ import annotations

import cv2
import numpy as np

from .compositor import erode_mask, expand_mask


def segment_changed_object(
    baseline: np.ndarray,
    candidate: np.ndarray,
    editable_mask: np.ndarray,
    target_seed: np.ndarray,
    *,
    difference_threshold: int = 18,
) -> tuple[np.ndarray, dict]:
    """Conservative local fallback when semantic segmentation is unavailable.

    It is deliberately recorded as a change-based fallback, not as SAM.  The
    selected components must overlap the old target, which rejects unrelated
    provider drift elsewhere in the approved window.
    """
    if candidate.shape[:2] != baseline.shape[:2]:
        candidate = cv2.resize(
            candidate, (baseline.shape[1], baseline.shape[0]),
            interpolation=cv2.INTER_LANCZOS4,
        )
    delta = np.max(
        np.abs(candidate.astype(np.int16) - baseline.astype(np.int16)), axis=2,
    )
    changed = np.where(
        (delta >= max(1, difference_threshold)) & (editable_mask > 0), 255, 0,
    ).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, kernel, iterations=2)
    changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, kernel, iterations=1)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (changed > 0).astype(np.uint8), connectivity=8,
    )
    keep = np.zeros_like(changed)
    seed = expand_mask(target_seed, 4) > 0
    kept_components = 0
    for index in range(1, count):
        component = labels == index
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 16:
            continue
        if np.any(component & seed):
            keep[component] = 255
            kept_components += 1
    if not np.any(keep):
        keep = changed
    return keep, {
        "provider": "local-change-fallback",
        "difference_threshold": max(1, difference_threshold),
        "kept_components": kept_components,
        "coverage": round(float((keep > 0).mean()), 6),
    }


def build_clean_plate_mask(
    target_mask: np.ndarray,
    foreground_restore_mask: np.ndarray,
    *,
    cleanup_radius_px: int = 10,
) -> np.ndarray:
    """Return the region that must be cleared before generating a replacement.

    The old object is expanded slightly to remove coloured outlines and shadows.
    Protected foreground interiors are never sent to LaMa, while the small
    underlap already encoded in ``foreground_restore_mask`` remains available.
    """
    cleanup = expand_mask(target_mask, max(0, cleanup_radius_px))
    return np.where(
        (cleanup > 0) & (foreground_restore_mask == 0), 255, 0,
    ).astype(np.uint8)


def build_semantic_alpha(
    target_mask: np.ndarray,
    result_object_mask: np.ndarray | None,
    editable_mask: np.ndarray,
    *,
    cleanup_radius_px: int = 10,
    result_radius_px: int = 2,
    edge_width_px: int = 6,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a trimap-guided alpha that never changes pixels outside approval.

    The cleanup region removes the previous object.  The re-segmented result can
    extend beyond it, but is still clamped to the user-approved editable area.
    Only the inside edge is softened, so all external pixels remain byte-exact.
    """
    cleanup = expand_mask(target_mask, max(0, cleanup_radius_px))
    semantic = cleanup
    if result_object_mask is not None:
        semantic = np.maximum(
            semantic,
            expand_mask(result_object_mask, max(0, result_radius_px)),
        )
    commit = np.where(
        (semantic > 0) & (editable_mask > 0), 255, 0,
    ).astype(np.uint8)

    width = max(0, edge_width_px)
    if width == 0:
        alpha = commit.copy()
        trimap = np.where(commit > 0, 255, 0).astype(np.uint8)
        return commit, alpha, trimap

    core = erode_mask(commit, width)
    uncertain = (commit > 0) & (core == 0)
    distance = cv2.distanceTransform((commit > 0).astype(np.uint8), cv2.DIST_L2, 5)
    alpha_float = np.clip(distance / float(width), 0.0, 1.0)
    alpha_float[commit == 0] = 0.0
    alpha_float[core > 0] = 1.0
    alpha = np.rint(alpha_float * 255.0).astype(np.uint8)

    trimap = np.zeros_like(commit, dtype=np.uint8)
    trimap[uncertain] = 128
    trimap[core > 0] = 255
    return commit, alpha, trimap


def alpha_composite_with_foreground(
    original: np.ndarray,
    candidate: np.ndarray,
    alpha_mask: np.ndarray,
    foreground_restore_mask: np.ndarray,
) -> np.ndarray:
    """Blend a candidate and then restore protected foreground byte-for-byte."""
    h, w = original.shape[:2]
    if candidate.shape[:2] != (h, w):
        candidate = cv2.resize(candidate, (w, h), interpolation=cv2.INTER_LANCZOS4)
    alpha = (alpha_mask.astype(np.float32) / 255.0)[:, :, None]
    result = (
        original.astype(np.float32) * (1.0 - alpha)
        + candidate.astype(np.float32) * alpha
    )
    result = np.clip(result, 0, 255).astype(np.uint8)
    protected = foreground_restore_mask > 0
    result[protected] = original[protected]
    return result


def build_quality_report(
    original: np.ndarray,
    result: np.ndarray,
    alpha_mask: np.ndarray,
    approved_envelope: np.ndarray,
    foreground_restore_mask: np.ndarray,
    *,
    result_object_mask: np.ndarray | None = None,
) -> dict:
    """Compute deterministic safety checks for an object-edit result."""
    pixel_diff = np.max(
        np.abs(result.astype(np.int16) - original.astype(np.int16)), axis=2,
    )
    outside = approved_envelope == 0
    protected = foreground_restore_mask > 0
    changed = pixel_diff > 0
    outside_changed = int(np.count_nonzero(changed & outside))
    protected_changed = int(np.count_nonzero(changed & protected))
    changed_total = int(np.count_nonzero(changed))
    uncertain = (alpha_mask > 0) & (alpha_mask < 255)
    report = {
        "contract": "catsco.object-edit-quality.v1",
        "outside_changed_pixels": outside_changed,
        "protected_changed_pixels": protected_changed,
        "changed_pixels": changed_total,
        "soft_edge_pixels": int(np.count_nonzero(uncertain)),
        "alpha_coverage": round(float((alpha_mask > 0).mean()), 6),
        "approved_envelope_coverage": round(float((approved_envelope > 0).mean()), 6),
        "result_object_coverage": (
            round(float((result_object_mask > 0).mean()), 6)
            if result_object_mask is not None else None
        ),
    }
    report["safety_passed"] = outside_changed == 0 and protected_changed == 0
    return report
