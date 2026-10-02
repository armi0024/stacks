from __future__ import annotations

import json

from stacks.analyzer.analyze import analyze_pdf
from stacks.analyzer.config import AnalyzerSettings, analysis_config_hash, load_settings


def test_stable_for_same_settings():
    assert analysis_config_hash(AnalyzerSettings()) == analysis_config_hash(AnalyzerSettings())


def test_any_setting_change_changes_hash():
    base = analysis_config_hash(AnalyzerSettings())
    assert analysis_config_hash(AnalyzerSettings(image_adequacy_threshold=76.0)) != base
    assert analysis_config_hash(AnalyzerSettings(text_fitness_if_weight=0.6)) != base
    assert analysis_config_hash(AnalyzerSettings(available_languages=("eng",))) != base


def test_load_settings_overrides(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"available_languages": ["eng"], "image_adequacy_threshold": 80}))
    s = load_settings(str(cfg))
    assert s.available_languages == ("eng",)
    assert s.image_adequacy_threshold == 80


def test_hash_recorded_in_result(fixtures):
    r = analyze_pdf(fixtures.get("blank_and_text"), lang_override="eng")
    assert r.config_hash == analysis_config_hash(AnalyzerSettings())
