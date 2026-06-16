from __future__ import annotations

import argparse
import json
import os
import sys

from .ocr import parse_pdf


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lex-ocr",
        description="Convert a PDF to markdown using Lex Hermes MinerU OCR.",
    )
    parser.add_argument("pdf", help="Path to the PDF file, or a directory of PDFs.")
    parser.add_argument("--language", default="ch", help="Language code, default: ch.")
    parser.add_argument("--page-range", help="Pages such as 1-5 or 1,3,5-7.")
    parser.add_argument(
        "--model-version",
        default="vlm",
        help="MinerU precision model: vlm, pipeline, or MinerU-HTML.",
    )
    parser.add_argument("--agent", action="store_true", help="Prefer free Agent API.")
    parser.add_argument("--json", action="store_true", help="Print full JSON result.")
    parser.add_argument("--out", help="Write markdown text to this path.")
    args = parser.parse_args()

    pdf_path = args.pdf.rstrip("/")

    # Directory: batch-process all PDFs
    if os.path.isdir(pdf_path):
        import glob as _glob
        pdfs = sorted(_glob.glob(os.path.join(pdf_path, "*.pdf")))
        if not pdfs:
            print(f"No PDF files found in directory: {pdf_path}", file=sys.stderr)
            return 1
        combined: list[str] = []
        all_ok = True
        for i, p in enumerate(pdfs):
            fname = os.path.basename(p)
            print(f"[{i+1}/{len(pdfs)}] {fname}...", file=sys.stderr)
            result = parse_pdf(
                p, language=args.language, page_range=args.page_range,
                model_version=args.model_version, prefer_precise=not args.agent,
            )
            if result.get("ok"):
                md = result.get("markdown") or result.get("text") or ""
                combined.append(f"## {fname}\n\n{md}")
            else:
                combined.append(f"## {fname}\n\n(OCR failed: {result.get('error', 'unknown')})")
                all_ok = False
        full_text = "\n\n---\n\n".join(combined)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(full_text)
        else:
            print(full_text)
        return 0 if all_ok else 1

    result = parse_pdf(
        args.pdf,
        language=args.language,
        page_range=args.page_range,
        model_version=args.model_version,
        prefer_precise=not args.agent,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("ok"):
        text = result.get("markdown") or result.get("text") or ""
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        else:
            print(text)
    else:
        print(result.get("error", "OCR failed"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
