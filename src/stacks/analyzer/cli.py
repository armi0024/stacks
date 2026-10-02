"""stacks-analyze: standalone analyzer CLI (SPEC 6.1, milestone 1a)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stacks.analyzer.analyze import analyze_pdf
from stacks.analyzer.config import load_settings
from stacks.analyzer.report import human_summary, to_json


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stacks-analyze",
        description="Measure a PDF representation: text provenance, image fidelity, "
        "text quality, adequacy, page gates, and recommended action (SPEC v1.5.1 6.1/6.3).",
    )
    p.add_argument("pdf", help="path to the PDF to analyze")
    p.add_argument(
        "--mode",
        choices=["assess", "screening"],
        default="assess",
        help="assess = comprehensive (all pages, default); screening = up to 16 sampled pages, provisional",
    )
    p.add_argument(
        "--lang",
        default=None,
        help="operator language override (e.g. eng, jpn); default: detect on first pages",
    )
    p.add_argument(
        "--identity-lang",
        default=None,
        help="identity-field language used as default when detection is not confident",
    )
    p.add_argument("--json", dest="json_out", default=None, help="write the full JSON report here")
    p.add_argument(
        "--measure-achievable",
        action="store_true",
        help="re-OCR suspect embedded OCR layers to measure achievable (post-REPROCESS) quality",
    )
    p.add_argument(
        "--critical-pages",
        default=None,
        help="comma-separated 1-based page numbers to treat as operator-designated critical pages",
    )
    p.add_argument(
        "--schematic-implied",
        action="store_true",
        help="assert the document should contain schematics (critical-content check)",
    )
    p.add_argument("--config", default=None, help="JSON file of AnalyzerSettings overrides")
    p.add_argument("--quiet", action="store_true", help="suppress the human-readable summary")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"error: not a file: {pdf_path}", file=sys.stderr)
        return 2
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError, TypeError) as e:
        print(f"error: bad config: {e}", file=sys.stderr)
        return 2
    critical: set[int] = set()
    if args.critical_pages:
        try:
            critical = {int(x) - 1 for x in args.critical_pages.split(",") if x.strip()}
        except ValueError:
            print("error: --critical-pages must be comma-separated page numbers", file=sys.stderr)
            return 2
    try:
        result = analyze_pdf(
            str(pdf_path),
            settings=settings,
            mode=args.mode,
            lang_override=args.lang,
            identity_language=args.identity_lang,
            measure_achievable=args.measure_achievable,
            critical_pages=critical,
            schematic_implied_flag=args.schematic_implied,
        )
    except Exception as e:  # noqa: BLE001 - CLI boundary
        print(f"error: analysis failed: {e}", file=sys.stderr)
        return 1
    if args.json_out:
        Path(args.json_out).write_text(to_json(result), encoding="utf-8")
    if not args.quiet:
        print(human_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
