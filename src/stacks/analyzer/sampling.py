"""Screening page sampling: up to N evenly sampled pages, indices recorded.

Unsampled pages are UNASSESSED (SPEC 6.1); sampled indices travel with the
report so provisional coverage is auditable.
"""

from __future__ import annotations


def screening_sample(page_count: int, max_pages: int) -> list[int]:
    if page_count <= max_pages:
        return list(range(page_count))
    if max_pages == 1:
        return [0]
    step = (page_count - 1) / (max_pages - 1)
    indices = sorted({round(i * step) for i in range(max_pages)})
    return indices
