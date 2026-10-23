"""Command-line entry points: pdf-form-inspect, pdf-form-fill, pdf-form-verify."""

from __future__ import annotations

import argparse
import json
import sys

from .fill import fill_form, inspect_form
from .legibility import main as verify_main


def inspect_main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="List fillable fields in a PDF form.")
    ap.add_argument("pdf")
    a = ap.parse_args(argv)
    print(json.dumps(inspect_form(a.pdf), indent=2))
    return 0


def fill_main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fill a PDF form from a JSON file of {field: value}.")
    ap.add_argument("source_pdf")
    ap.add_argument("output_pdf")
    ap.add_argument("values_json")
    ap.add_argument("--loose", action="store_true", help="skip unknown field names instead of refusing")
    ap.add_argument("--finalize", action="store_true")
    a = ap.parse_args(argv)
    with open(a.values_json, encoding="utf-8") as f:
        values = json.load(f)
    res = fill_form(a.source_pdf, a.output_pdf, values, strict=not a.loose, finalize=a.finalize)
    print(json.dumps(res, indent=2))
    return 0 if res.get("status") == "ok" else (2 if res.get("status") == "refused" else 1)


if __name__ == "__main__":
    sys.exit(verify_main())
