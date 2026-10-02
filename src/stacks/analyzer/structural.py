"""Structural pass: compression filters and inherited risk flags.

jbig2_unknown_history (SPEC 6.1 risk flags): acquired JBIG2 without recorded
lossless provenance carries the flag — no numeric deduction, surfaced in the
report and reasons; it blocks CONFIRMED text-default status downstream and is
never cleansed by our own recompression. Standalone, every detected JBIG2
stream is "unknown history" (there is no provenance store yet to consult).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf


@dataclass
class StructuralFindings:
    filters_seen: set[str] = field(default_factory=set)
    jbig2_xrefs: list[int] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _filters_for_xref(doc: pymupdf.Document, xref: int) -> list[str]:
    ftype, value = doc.xref_get_key(xref, "Filter")
    if ftype == "null" or not value:
        return []
    # value looks like "/JBIG2Decode" or "[/FlateDecode/DCTDecode]"
    return [tok for tok in value.replace("[", " ").replace("]", " ").split("/") if tok.strip()]


def scan(doc: pymupdf.Document) -> StructuralFindings:
    findings = StructuralFindings()
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Subtype")[1] != "/Image":
                continue
            filters = _filters_for_xref(doc, xref)
        except Exception:  # noqa: BLE001 - malformed objects must not stop the scan
            continue
        findings.filters_seen.update(filters)
        if "JBIG2Decode" in filters:
            findings.jbig2_xrefs.append(xref)
    if findings.jbig2_xrefs:
        findings.risk_flags.append("jbig2_unknown_history")
        findings.reasons.append(
            f"JBIG2 compression detected in {len(findings.jbig2_xrefs)} image stream(s) "
            "without recorded lossless provenance: risk flag jbig2_unknown_history set "
            "(no score deduction; blocks CONFIRMED text-default status until an operator "
            "acknowledges it; recompression never clears an inherited flag)."
        )
    return findings
