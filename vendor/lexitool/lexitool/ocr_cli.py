from __future__ import annotations

import argparse
import json
import sys

from .ocr import parse_pdf


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lex-ocr",
        description="Convert a PDF to markdown using Lex Hermes MinerU OCR.",
    )
    parser.add_argument("pdf", help="Path to the PDF file.")
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
