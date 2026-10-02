"""Text-quality primaries (SPEC 6.1).

Fresh OCR (image-only pages, or --measure-achievable re-OCR) yields the full
per-word confidence distribution, small-text and refdes-token confidences.
Embedded text (native-text and as-shipped OCR layers) carries no confidence
data, so it is scored by extraction-quality checks (dictionary/word-shape
garble detection) — the same family SPEC mandates for native-text pages.
A suspect embedded layer is labeled explicitly: the as-shipped TQ may
understate what a re-OCR could achieve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from stacks.analyzer.ocr import OcrWord

MIN_WORDS_FOR_TQ = 5

REFDES_RE = re.compile(r"^(?:[RCLQDJKWFY]|CR|IC|U|VR|TP|XT)\d{1,4}[A-Za-z]?$")
PARTNUM_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,}$")

_COMMON_WORDS = frozenset(
    """the of and to in a is that for it as was with be by on not he this are or his
    from at which but have an they you were her she all there their one we him been
    has when who will no more if out so up said what its about than into them can
    only other time new some could these two may first then do any like my now over
    such our man me even most made after also did many off before must well back
    through years where much your way down should because each just those people how
    too little good make world still see own men work long here get both between life
    being under never day same another know while last might us great old year come
    since against go came right used take three page section figure table manual
    board power supply circuit signal voltage input output ground test adjust check
    replace remove install connect wire switch control game play coin monitor video
    sound amplifier transistor resistor capacitor diode assembly parts list number""".split()
)

_VOWELS = set("aeiouy")


@dataclass
class TQMetrics:
    source: str  # ocr-fresh | embedded-ocr-layer | native-text
    word_count: int = 0
    mean_conf: float | None = None
    low_conf_fraction: float | None = None
    small_text_conf: float | None = None
    small_text_count: int = 0
    refdes_conf: float | None = None
    refdes_count: int = 0
    garble_fraction: float | None = None
    suspect: bool = False
    null: bool = False
    null_reason: str | None = None
    notes: list[str] = field(default_factory=list)


def null_tq(source: str, reason: str) -> TQMetrics:
    return TQMetrics(source=source, null=True, null_reason=reason)


def is_refdes_token(token: str) -> bool:
    t = token.strip(".,;:()[]")
    if REFDES_RE.match(t):
        return True
    return bool(PARTNUM_RE.match(t) and any(c.isdigit() for c in t) and any(c.isalpha() for c in t))


def _max_consonant_run(word: str) -> int:
    run = best = 0
    for c in word:
        if c in _VOWELS:
            run = 0
        else:
            run += 1
            best = max(best, run)
    return best


def is_plausible_word(token: str) -> bool | None:
    """True/False for alphabetic tokens; None when the token doesn't count."""
    t = token.strip(".,;:()[]\"'!?").lower()
    if len(t) < 1 or not t.isalpha() or not t.isascii():
        return None
    if t in _COMMON_WORDS or len(t) <= 2:
        return True
    if len(t) > 20:
        return False
    if not any(c in _VOWELS for c in t):
        return False
    return _max_consonant_run(t) <= 4


def garble_fraction(tokens: list[str]) -> float | None:
    """Fraction of counted alphabetic tokens that are implausible (eng only)."""
    verdicts = [v for v in (is_plausible_word(t) for t in tokens) if v is not None]
    if len(verdicts) < MIN_WORDS_FOR_TQ:
        return None
    return sum(1 for v in verdicts if not v) / len(verdicts)


def from_ocr(words: list[OcrWord], small_text_px: float, low_conf_threshold: float) -> TQMetrics:
    if len(words) < MIN_WORDS_FOR_TQ:
        return null_tq("ocr-fresh", f"fewer than {MIN_WORDS_FOR_TQ} words recognized: too little text to measure")
    confs = [w.conf for w in words]
    small = [w.conf for w in words if w.height <= small_text_px]
    refdes = [w.conf for w in words if is_refdes_token(w.text)]
    return TQMetrics(
        source="ocr-fresh",
        word_count=len(words),
        mean_conf=sum(confs) / len(confs),
        low_conf_fraction=sum(1 for c in confs if c < low_conf_threshold) / len(confs),
        small_text_conf=(sum(small) / len(small)) if small else None,
        small_text_count=len(small),
        refdes_conf=(sum(refdes) / len(refdes)) if refdes else None,
        refdes_count=len(refdes),
    )


def from_embedded(
    tokens: list[str], source: str, language: str | None, suspect_threshold: float
) -> TQMetrics:
    """Extraction-quality checks on an embedded (native or OCR-layer) text layer."""
    if language != "eng":
        return null_tq(
            source,
            "extraction-quality checks unavailable for this language: "
            "text quality unknown, not penalized",
        )
    g = garble_fraction(tokens)
    if g is None:
        return null_tq(source, "too little alphabetic text to run extraction-quality checks")
    m = TQMetrics(source=source, word_count=len(tokens), garble_fraction=g)
    if g >= suspect_threshold and source == "embedded-ocr-layer":
        m.suspect = True
        m.notes.append(
            "text layer suspect; achievable quality unknown; rerun with --measure-achievable"
        )
    return m
