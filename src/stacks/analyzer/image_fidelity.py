"""Image-fidelity primaries, recorded separately per page (SPEC 6.1).

Effective DPI comes from the dominant raster's rendered placement (P17);
sharpness is Laplacian variance on the grayscale render at min(native, 300)
DPI (45 MP cap); speckle is tiny isolated dark components per 100k pixels;
skew is the median text-line / long-line angle. Missing measurements stay
None — unknown, never imputed.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from stacks.analyzer.content_class import INK_THRESHOLD

SPECKLE_MAX_AREA_PX = 5


@dataclass
class IFMetrics:
    effective_dpi: float | None
    bpc: int | None
    is_1bit: bool
    sharpness: float | None
    rms_contrast: float | None
    speckle_index: float | None
    skew_deg: float | None
    render_dpi: float | None


def sharpness_laplacian(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def rms_contrast(gray: np.ndarray) -> float:
    return float(gray.std())


def speckle_index(gray: np.ndarray) -> float:
    """Tiny isolated dark components per 100k pixels."""
    binary = (gray < INK_THRESHOLD).astype(np.uint8)
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(binary)
    tiny = sum(1 for i in range(1, n) if stats[i][cv2.CC_STAT_AREA] <= SPECKLE_MAX_AREA_PX)
    return tiny / (gray.size / 1e5)


def skew_degrees(gray: np.ndarray) -> float | None:
    """Median absolute skew of merged text rows / long lines, in degrees."""
    binary = (gray < INK_THRESHOLD).astype(np.uint8) * 255
    if cv2.countNonZero(binary) < 50:
        return None
    h, w = binary.shape
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 50), 3))
    merged = cv2.dilate(binary, kernel)
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    angles: list[float] = []
    weights: list[float] = []
    for c in contours:
        if cv2.contourArea(c) < 200:
            continue
        (cx, cy), (rw, rh), angle = cv2.minAreaRect(c)
        long_side, short_side = max(rw, rh), min(rw, rh)
        if short_side == 0 or long_side / short_side < 3:
            continue
        # minAreaRect angle: normalize so 0 = horizontal, range (-45, 45]
        if rw < rh:
            angle += 90.0
        while angle > 45.0:
            angle -= 90.0
        while angle <= -45.0:
            angle += 90.0
        angles.append(angle)
        weights.append(long_side)
    if not angles:
        return None
    order = np.argsort(angles)
    cum = np.cumsum(np.asarray(weights)[order])
    median_angle = np.asarray(angles)[order][int(np.searchsorted(cum, cum[-1] / 2))]
    return abs(float(median_angle))


def measure(
    gray: np.ndarray | None,
    effective_dpi: float | None,
    bpc: int | None,
    render_dpi: float | None,
) -> IFMetrics:
    if gray is None:
        return IFMetrics(effective_dpi, bpc, bpc == 1, None, None, None, None, render_dpi)
    return IFMetrics(
        effective_dpi=effective_dpi,
        bpc=bpc,
        is_1bit=bpc == 1,
        sharpness=sharpness_laplacian(gray),
        rms_contrast=rms_contrast(gray),
        speckle_index=speckle_index(gray),
        skew_deg=skew_degrees(gray),
        render_dpi=render_dpi,
    )
