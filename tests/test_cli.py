from __future__ import annotations

import json

from stacks.analyzer.cli import main


def test_json_report(fixtures, tmp_path, capsys):
    out = tmp_path / "report.json"
    rc = main([fixtures.get("blank_and_text"), "--lang", "eng", "--json", str(out)])
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["report_schema_version"] == "1"
    assert report["analysis_config_hash"]
    assert report["file"]["page_count"] == 2
    assert report["coverage"]["mode"] == "assess"
    assert len(report["pages"]) == 2
    assert report["recommended_action"]["action"] in ("USABLE", "REPROCESS", "RESCAN_CANDIDATE")
    assert report["document_scores"]["adequacy_basis"].startswith("primary measurements")
    summary = capsys.readouterr().out
    assert "RECOMMENDED ACTION" in summary


def test_screening_mode(fixtures, tmp_path):
    out = tmp_path / "r.json"
    rc = main([fixtures.get("many_pages"), "--lang", "eng", "--mode", "screening",
               "--json", str(out), "--quiet"])
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["coverage"]["mode"] == "screening"
    assert report["coverage"]["assessed_pages"] == 16
    assert report["coverage"]["unassessed_pages"] == 24
    assert report["recommended_action"]["provisional"] is True
    assert "provisional" in report["recommended_action"]["labels"]


def test_config_file_language_restriction(fixtures, tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"available_languages": ["eng"]}))
    out = tmp_path / "r.json"
    rc = main([fixtures.get("wrong_language"), "--config", str(cfg),
               "--json", str(out), "--quiet"])
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["language"]["supported"] is False
    assert report["document_scores"]["tq_text"] is None
    assert report["recommended_action"]["action"] == "USABLE"


def test_critical_pages_flag(fixtures, tmp_path):
    out = tmp_path / "r.json"
    rc = main([fixtures.get("ocr_layer_no_floor"), "--lang", "eng",
               "--critical-pages", "1", "--json", str(out), "--quiet"])
    assert rc == 0
    report = json.loads(out.read_text())
    assert 1 in report["page_gates"]["required_pages"]
    assert report["page_gates"]["blocking"]  # IF ~45 < 50 on the designated page


def test_missing_file_exit_2(tmp_path):
    assert main([str(tmp_path / "nope.pdf")]) == 2


def test_bad_critical_pages_exit_2(fixtures):
    assert main([fixtures.get("blank_and_text"), "--critical-pages", "a,b"]) == 2
