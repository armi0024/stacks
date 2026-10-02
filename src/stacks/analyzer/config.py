"""Analyzer settings and the full analysis configuration hash.

Comparability is keyed on the FULL analysis configuration (code version +
settings + weights), not code version alone (SPEC v1.5 change 12). Every
report records analysis_config_hash; scores are comparable only within it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from stacks import CODE_VERSION


@dataclass(frozen=True)
class AnalyzerSettings:
    # --- image fidelity (SPEC 6.1 derived per-page scores) ---
    dpi_target_schematic: float = 300.0
    dpi_target_text: float = 220.0
    dpi_deduction_max: float = 40.0
    dpi_low_threshold: float = 150.0
    dpi_low_deduction: float = 10.0
    sharpness_min: float = 120.0
    sharpness_deduction: float = 15.0
    speckle_max: float = 6.0
    speckle_deduction: float = 8.0
    skew_max_deg: float = 1.5
    skew_deduction: float = 6.0
    onebit_schematic_deduction: float = 12.0
    render_dpi_cap: float = 300.0
    render_mp_cap: float = 45.0

    # --- text quality ---
    conf_mean_target: float = 80.0
    conf_mean_deduction_max: float = 30.0
    low_conf_threshold: float = 60.0
    low_conf_frac_target: float = 0.15
    low_conf_frac_full_at: float = 0.50
    low_conf_frac_deduction_max: float = 15.0
    small_text_px: float = 18.0
    small_text_conf_min: float = 70.0
    small_text_deduction: float = 10.0
    refdes_conf_min: float = 75.0
    refdes_deduction: float = 12.0
    garble_deduction_max: float = 40.0
    # extraction-quality (garble) score above this marks the layer suspect
    suspect_garble_fraction: float = 0.30

    # --- adequacy (primary, per dimension; SPEC 6.1) ---
    image_adequacy_threshold: float = 75.0
    text_adequacy_threshold: float = 70.0

    # --- fitness blends (ranking only) ---
    text_fitness_if_weight: float = 0.5
    text_fitness_tq_weight: float = 0.5
    schem_fitness_if_weight: float = 0.7
    schem_fitness_tq_weight: float = 0.3

    # --- grades (display only) ---
    grade_a: float = 88.0
    grade_b: float = 78.0
    grade_c: float = 68.0
    grade_d: float = 55.0

    # --- two-level page gates (SPEC P6/P15) ---
    page_gate_block_threshold: float = 50.0

    # --- content classification ---
    blank_ink_fraction: float = 0.005

    # --- screening (SPEC 6.1 modes) ---
    screening_max_pages: int = 16

    # --- OCR language handling (SPEC P2) ---
    available_languages: tuple[str, ...] = ("eng", "jpn", "jpn_vert")
    language_detect_pages: int = 3

    def to_canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def analysis_config_hash(settings: AnalyzerSettings) -> str:
    payload = CODE_VERSION + "\n" + settings.to_canonical_json()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_settings(path: str | None = None, **overrides) -> AnalyzerSettings:
    """Load settings from a JSON file (optional) with keyword overrides."""
    data: dict = {}
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    data.update(overrides)
    if "available_languages" in data:
        data["available_languages"] = tuple(data["available_languages"])
    return AnalyzerSettings(**data)
