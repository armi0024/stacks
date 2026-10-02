"""Tesseract integration: TSV OCR, OSD script detection, language resolution.

SPEC P2: language is detected on first pages and set per document, operator
overridable. When the resolved language is unknown or unsupported (not in the
configured available set), TQ is null — unknown, never a penalty.
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

_SCRIPT_TO_LANG = {
    "Latin": "eng",
    "Japanese": "jpn",
    "Japanese_vert": "jpn_vert",
    "Han": "chi_sim",
    "Hangul": "kor",
    "Cyrillic": "rus",
}


@dataclass
class OcrWord:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


@lru_cache(maxsize=1)
def installed_languages() -> frozenset[str]:
    try:
        out = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    langs = [ln.strip() for ln in out.stdout.splitlines()[1:] if ln.strip()]
    return frozenset(langs)


def _run_tesseract(image: np.ndarray, args: list[str], timeout: float = 180.0) -> str:
    with tempfile.TemporaryDirectory(prefix="stacks-ocr-") as tmp:
        png = Path(tmp) / "page.png"
        cv2.imwrite(str(png), image)
        proc = subprocess.run(
            ["tesseract", str(png), "stdout", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[:300]}")
        return proc.stdout


def ocr_tsv(image: np.ndarray, lang: str) -> list[OcrWord]:
    """OCR a grayscale page image; return word-level results with confidences."""
    out = _run_tesseract(image, ["-l", lang, "tsv"])
    words: list[OcrWord] = []
    reader = csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        try:
            if int(row["level"]) != 5:
                continue
            conf = float(row["conf"])
        except (KeyError, TypeError, ValueError):
            continue
        text = (row.get("text") or "").strip()
        if conf < 0 or not text:
            continue
        words.append(
            OcrWord(
                text=text,
                conf=conf,
                left=int(row["left"]),
                top=int(row["top"]),
                width=int(row["width"]),
                height=int(row["height"]),
            )
        )
    return words


def detect_script(image: np.ndarray) -> str | None:
    """OSD script detection; returns a language code hint or None."""
    try:
        out = _run_tesseract(image, ["--psm", "0", "-l", "osd"], timeout=60)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if line.startswith("Script:"):
            script = line.split(":", 1)[1].strip()
            return _SCRIPT_TO_LANG.get(script)
    return None


def detect_language_from_text(text: str) -> str | None:
    """Unicode-range language detection for native/embedded text."""
    if not text:
        return None
    kana = sum(1 for c in text if "぀" <= c <= "ヿ")
    han = sum(1 for c in text if "一" <= c <= "鿿")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    letters = kana + han + latin
    if letters < 10:
        return None
    if kana / letters > 0.05:
        return "jpn"
    if han / letters > 0.5:
        return None  # CJK without kana: ambiguous (chi/jpn) -> unknown
    if latin / letters > 0.8:
        return "eng"
    return None
