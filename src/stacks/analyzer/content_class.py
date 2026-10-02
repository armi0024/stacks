"""Per-page content class: blank | text | schematic | mixed, plus table regions.

Heuristics on the grayscale render: ink fraction for blank, long straight-line
density for schematic structure, word-box coverage for text density. Table
regions (ruled grids) are recorded as bboxes in render pixels (best-effort;
clues for the chunker, not proof).

KNOWN LIMITATION (smoke run 2026-10-01, disposition 4): wiring diagrams with
mostly short/curved runs under-classify as text-class (the long-straight-line
score misses them), and diagonal-only drawings are under-detected by the
morphological H/V kernels. Threshold calibration belongs to the pilot
ground-truth set (SPEC 6.1). Operator mitigations exist today:
--schematic-implied holds every page to the schematic DPI target, and
--critical-pages applies the page gates to designated pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

INK_THRESHOLD = 160  # gray level below which a pixel counts as ink
LINE_SCORE_SCHEMATIC = 3.0  # total long-line length, in page widths
TEXT_COVER_LOW = 0.10
MIN_LINE_FRAC = 0.15  # a "long" line spans >= 15% of the smaller page side


@dataclass
class ContentResult:
    content_class: str
    ink_fraction: float
    line_score: float
    text_coverage: float
    table_regions: list[tuple[int, int, int, int]] = field(default_factory=list)


def _long_line_score(gray: np.ndarray) -> float:
    """Total drawn straight-line length, in page widths.

    Morphological opening with long thin kernels: text rows are broken by
    word gaps and do not survive; ruled/drawn lines do. Length is estimated
    from surviving pixels divided by an assumed ~2 pt stroke thickness
    (diagonal-only drawings are under-detected; a known limitation).
    """
    h, w = gray.shape
    binary = (gray < INK_THRESHOLD).astype(np.uint8) * 255
    min_len = int(MIN_LINE_FRAC * min(h, w))
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (min_len, 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len))
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, hk)
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vk)
    line_px = cv2.countNonZero(horiz) + cv2.countNonZero(vert)
    thickness_est = max(1.0, h / 400.0)  # ~2 pt stroke at this render size
    return line_px / thickness_est / w


def _text_coverage(gray_shape: tuple[int, int], word_boxes: list[tuple[float, float, float, float]]) -> float:
    h, w = gray_shape
    if not word_boxes or h == 0 or w == 0:
        return 0.0
    # coarse occupancy grid to avoid double counting overlaps
    grid = np.zeros((64, 64), dtype=bool)
    for x0, y0, x1, y1 in word_boxes:
        gx0 = int(np.clip(x0 / w * 64, 0, 63))
        gx1 = int(np.clip(x1 / w * 64, 0, 63))
        gy0 = int(np.clip(y0 / h * 64, 0, 63))
        gy1 = int(np.clip(y1 / h * 64, 0, 63))
        grid[gy0 : gy1 + 1, gx0 : gx1 + 1] = True
    return float(grid.mean())


def _table_regions(binary: np.ndarray) -> list[tuple[int, int, int, int]]:
    h, w = binary.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w // 30), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h // 30)))
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, hk)
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vk)
    inter = cv2.bitwise_and(horiz, vert)
    grid = cv2.bitwise_or(horiz, vert)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grid)
    regions: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        x, y, cw, ch, _area = stats[i]
        if cw < w * 0.1 or ch < h * 0.05:
            continue
        crossings = int(cv2.countNonZero(inter[y : y + ch, x : x + cw]))
        if crossings >= 4:
            regions.append((int(x), int(y), int(cw), int(ch)))
    return regions


def classify_content(
    gray: np.ndarray,
    word_boxes_px: list[tuple[float, float, float, float]],
    blank_ink_fraction: float,
) -> ContentResult:
    ink = float((gray < INK_THRESHOLD).mean())
    if ink < blank_ink_fraction:
        return ContentResult("blank", ink, 0.0, 0.0)
    line_score = _long_line_score(gray)
    text_cov = _text_coverage(gray.shape, word_boxes_px)
    binary = (gray < INK_THRESHOLD).astype(np.uint8) * 255
    tables = _table_regions(binary)
    if line_score >= LINE_SCORE_SCHEMATIC and text_cov < TEXT_COVER_LOW:
        cls = "schematic"
    elif line_score >= LINE_SCORE_SCHEMATIC:
        cls = "mixed"
    else:
        cls = "text"
    return ContentResult(cls, ink, line_score, text_cov, tables)
