"""Report rendering: versioned JSON schema + human-readable summary.

Scores are comparable only within analysis_config_hash; the report carries
it alongside tool versions. Null means unknown, shown as unknown.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pymupdf

from stacks import CODE_VERSION
from stacks.analyzer.analyze import AnalysisResult, PageResult

REPORT_SCHEMA_VERSION = "1"


def _round(v, nd=1):
    return round(v, nd) if isinstance(v, float) else v


def _page_dict(p: PageResult) -> dict:
    d = {
        "page_index": p.index,
        "page_number": p.index + 1,
        "assessed": p.assessed,
        "page_type": p.page_type,
        "content_class": p.content_class,
        "image_fidelity": None,
        "if_score": _round(p.if_score),
        "if_deductions": [asdict(x) | {"points": _round(x.points)} for x in p.if_deductions],
        "text_quality": None,
        "tq_as_shipped_score": _round(p.tq_as_shipped_score),
        "tq_deductions": [asdict(x) | {"points": _round(x.points)} for x in p.tq_deductions],
        "tq_achievable_score": _round(p.tq_achievable_score),
        "tq_effective_score": _round(p.tq_score),
        "tq_null": p.tq_is_null,
        "table_regions": p.content.table_regions if p.content else [],
        "errors": p.errors,
    }
    if p.if_metrics:
        m = p.if_metrics
        d["image_fidelity"] = {
            "effective_dpi": _round(m.effective_dpi),
            "bpc": m.bpc,
            "is_1bit": m.is_1bit,
            "sharpness": _round(m.sharpness),
            "rms_contrast": _round(m.rms_contrast),
            "speckle_index": _round(m.speckle_index, 2),
            "skew_deg": _round(m.skew_deg, 2),
            "render_dpi": _round(m.render_dpi),
        }
    if p.tq:
        t = p.tq
        d["text_quality"] = {
            "source": t.source,
            "word_count": t.word_count,
            "mean_conf": _round(t.mean_conf),
            "low_conf_fraction": _round(t.low_conf_fraction, 3),
            "small_text_conf": _round(t.small_text_conf),
            "small_text_count": t.small_text_count,
            "refdes_conf": _round(t.refdes_conf),
            "refdes_count": t.refdes_count,
            "garble_fraction": _round(t.garble_fraction, 3),
            "suspect": t.suspect,
            "null": t.null,
            "null_reason": t.null_reason,
            "notes": t.notes,
        }
    return d


def to_dict(r: AnalysisResult) -> dict:
    sc = r.scores
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "tool": {
            "name": "stacks-analyze",
            "code_version": CODE_VERSION,
            "pymupdf_version": pymupdf.__version__,
        },
        "analysis_config_hash": r.config_hash,
        "file": {"path": r.path, "sha256": r.sha256, "page_count": r.page_count},
        "coverage": {
            "mode": r.mode,
            "sampled_indices": r.sampled_indices,
            "assessed_pages": len(r.sampled_indices),
            "unassessed_pages": r.page_count - len(r.sampled_indices),
        },
        "language": {
            "detected": r.language.detected,
            "used": r.language.used,
            "supported": r.language.supported,
            "reason": r.language.reason,
        },
        "structural": {
            "filters_seen": sorted(r.structural.filters_seen),
            "jbig2_image_count": len(r.structural.jbig2_xrefs),
            "risk_flags": r.structural.risk_flags,
            "reasons": r.structural.reasons,
        },
        "document_scores": {
            "if_text": _round(sc.if_text),
            "tq_text": _round(sc.tq_text),
            "if_schem": _round(sc.if_schem),
            "tq_schem": _round(sc.tq_schem),
            "image_adequate": sc.image_adequate(),
            "text_adequate": sc.text_adequate(),
            "adequacy_basis": "primary measurements only (never blended fitness)",
            "fitness_text_ranking_only": _round(sc.fitness_text),
            "fitness_schematic_ranking_only": _round(sc.fitness_schem),
            "grades": sc.grades,
            "text_page_count": sc.text_page_count,
            "schematic_page_count": sc.schem_page_count,
        },
        "page_gates": {
            "basis": r.gate_result.basis,
            "required_pages": [i + 1 for i in r.gate_result.required_pages],
            "blocking": [asdict(h) | {"score": _round(h.score)} for h in r.gate_result.blocking],
            "degraded": [asdict(h) | {"score": _round(h.score)} for h in r.gate_result.degraded],
            "pages_below_adequacy": r.gate_result.pages_below_adequacy,
        },
        "completeness": {
            "status": r.completeness.status,
            "missing_fraction": r.completeness.missing_fraction,
            "reasons": r.completeness.reasons,
        },
        "schematic_implied": r.schematic_implied,
        "recommended_action": {
            "action": r.recommendation.action,
            "labels": r.recommendation.labels,
            "provisional": r.recommendation.provisional,
            "reasons": r.recommendation.reasons,
        },
        "pages": [_page_dict(p) for p in r.pages],
    }


def to_json(r: AnalysisResult) -> str:
    return json.dumps(to_dict(r), indent=2)


def _fmt(v, suffix="") -> str:
    return "unknown" if v is None else f"{v:.0f}{suffix}" if isinstance(v, float) else f"{v}{suffix}"


def human_summary(r: AnalysisResult) -> str:
    sc = r.scores
    rec = r.recommendation
    lines = [
        f"stacks-analyze: {r.path}",
        f"  pages: {r.page_count}  mode: {r.mode}"
        + (f" (sampled {len(r.sampled_indices)})" if r.mode == "screening" else ""),
        f"  sha256: {r.sha256[:16]}...  config: {r.config_hash[:12]}...",
        f"  language: detected={r.language.detected or 'unknown'} used={r.language.used or 'none'}"
        f" supported={r.language.supported}",
        "",
        f"  image fidelity: text {_fmt(sc.if_text)} ({sc.grades.get('if_text') or '-'})"
        f"  schematic {_fmt(sc.if_schem)} ({sc.grades.get('if_schem') or '-'})",
        f"  text quality:  text {_fmt(sc.tq_text)} ({sc.grades.get('tq_text') or '-'})"
        f"  schematic {_fmt(sc.tq_schem)} ({sc.grades.get('tq_schem') or '-'})",
        f"  adequacy (primaries): image={_fmt(sc.image_adequate())} text={_fmt(sc.text_adequate())}",
        f"  pages below adequacy: image={r.gate_result.pages_below_adequacy.get('image', 0)}"
        f" text={r.gate_result.pages_below_adequacy.get('text', 0)}",
        f"  completeness: {r.completeness.status} (missing_fraction {r.completeness.missing_fraction})",
    ]
    if r.structural.risk_flags:
        lines.append(f"  risk flags: {', '.join(r.structural.risk_flags)}")
    lines.append("")
    label_txt = f"  [{', '.join(rec.labels)}]" if rec.labels else ""
    lines.append(f"  RECOMMENDED ACTION: {rec.action}{label_txt}")
    for reason in rec.reasons:
        lines.append(f"    - {reason}")
    for reason in r.structural.reasons:
        lines.append(f"    - {reason}")
    return "\n".join(lines)
