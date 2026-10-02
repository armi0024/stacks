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
    "Latin": "eng",  # placeholder: Latin script needs the wordlist vote to pick a language
    "Japanese": "jpn",
    "Japanese_vert": "jpn_vert",
    "Han": "chi_sim",
    "Hangul": "kor",
    "Cyrillic": "rus",
}

# Small high-frequency stopword lists for Latin-script language voting
# (disposition 3, 2026-10-01): cheap routing to the right tessdata instead of
# silently running under eng. eng is the fallback only when the vote is
# ambiguous, and the report records which language was actually used.
_LATIN_STOPWORDS: dict[str, frozenset[str]] = {
    "eng": frozenset(
        "the and of to in is for with on this that be are from or as it not "
        "by at an was have has will each which their there".split()
    ),
    "fra": frozenset(
        "le la les de des du et un une est pour dans sur que qui vous ne pas "
        "ce avec sont votre être par plus tout mais nous aux".split()
    ),
    "deu": frozenset(
        "der die das und ist nicht mit ein eine den von zu im für auf werden "
        "sie dem des sich bei oder wird nur auch nach".split()
    ),
    "spa": frozenset(
        "el los las de del y es en un una para con que no por se al como "
        "más este esta sobre cuando hay pero".split()
    ),
    "ita": frozenset(
        "il lo gli di del della e è un una per con che non sono nel alla "
        "questo come anche più dalla sulla ma".split()
    ),
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
    """OSD script detection; returns the raw script name (e.g. 'Latin',
    'Japanese') or None. Mapping to a language is the caller's job: Latin
    script needs a wordlist vote, not a direct eng assumption."""
    try:
        out = _run_tesseract(image, ["--psm", "0", "-l", "osd"], timeout=60)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if line.startswith("Script:"):
            return line.split(":", 1)[1].strip()
    return None


def script_to_language(script: str) -> str | None:
    return _SCRIPT_TO_LANG.get(script)


def detect_latin_language(text: str) -> tuple[str | None, bool]:
    """Wordlist vote among Latin-script languages.

    Returns (language, confident). confident=False means the vote was
    ambiguous and the caller should fall back to eng, recording that it did.
    """
    tokens = [t.strip(".,;:()[]\"'!?®™").lower() for t in text.split()]
    tokens = [t for t in tokens if t]
    if len(tokens) < 10:
        return None, False
    scores = {
        lang: sum(1 for t in tokens if t in words)
        for lang, words in _LATIN_STOPWORDS.items()
    }
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, best_n), (_, second_n) = ranked[0], ranked[1]
    if best_n < 3 or best_n < second_n * 1.5:
        return None, False
    return best, True


def detect_language_from_text(text: str) -> str | None:
    """Unicode-range + Latin-wordlist language detection for native text.

    Latin-script text is voted among the configured Latin languages; an
    ambiguous vote falls back to eng (recorded by the caller via
    detect_latin_language).
    """
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
        lang, confident = detect_latin_language(text)
        return lang if confident else "eng"
    return None
