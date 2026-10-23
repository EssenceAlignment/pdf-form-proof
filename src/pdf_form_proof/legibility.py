"""Prove that filled values are visible on the rendered page, not only
present in the PDF's hidden text layer.

Why: a PDF can carry a value in its data while the page a person sees (or
prints, or a reviewer opens) shows nothing, or shows it clipped. Every
text-based check passes on such a file. This check compares two channels:

  in text layer   seen by OCR of the render   verdict
  -------------   --------------------------  -----------------------------
  yes             yes                         VISIBLE
  yes             no                          INVISIBLE (in the file, not on the page)
  no              yes                         RENDER-ONLY (on the page, not in the data)
  no              no                          MISSING

Region scoping: pass a region (x0,y0,x1,y1 as fractions of the page) and only
that band is OCRd, so a copy of the value elsewhere on the page cannot make it
pass. With a region, "auto" mode also demands that every word the text layer
places inside the region is legible in the render, so the caller does not
have to remember which values to check.

Fail-closed rules: if pdftotext, pdftoppm or tesseract fails, the result is
REFUSE with the tool's own error, never a finding about the document. An
empty OCR result from a broken engine is not evidence a value is missing.

Limit: this proves legibility only. It says nothing about whether the values
are correct, complete, or appropriate for the form's purpose.

Requires the poppler utilities (pdftotext, pdftoppm) and the tesseract
binary on PATH. Region scoping also needs Pillow.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any


def norm(s: str) -> str:
    """Collapse to a comparable form: OCR confuses case, spacing and punctuation."""
    s = s.upper().replace("&", "AND")
    return re.sub(r"[^A-Z0-9]", "", s)


class _Refusal(str):
    """An empty result that carries the reason it is empty."""

    def __new__(cls, reason):
        s = super().__new__(cls, "")
        s.reason = reason
        return s


def _missing_binaries() -> list[str]:
    return [b for b in ("pdftotext", "pdftoppm", "tesseract") if shutil.which(b) is None]


def region_words(pdf: str, page: int, region) -> list[str] | None:
    """Words the text layer places fully inside the region (the auto set)."""
    try:
        out = subprocess.run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), pdf, "-"],
                             capture_output=True, timeout=120)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    xml = out.stdout.decode("utf-8", "replace")
    pm = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', xml)
    if not pm:
        return None
    PW, PH = float(pm.group(1)), float(pm.group(2))
    x0, y0, x1, y1 = region
    X0, Y0, X1, Y1 = x0 * PW, y0 * PH, x1 * PW, y1 * PH
    words = []
    for wx0, wy0, wx1, wy1, w in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', xml):
        # Full containment: a word straddling the edge would be sliced by the
        # crop and reported invisible when it is only half-photographed.
        if X0 <= float(wx0) and float(wx1) <= X1 and Y0 <= float(wy0) and float(wy1) <= Y1:
            t = w.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
            if t:
                words.append(t)
    return words


def text_layer(pdf: str, page: int):
    try:
        out = subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), pdf, "-"],
                             capture_output=True, timeout=120)
    except Exception as e:
        return _Refusal(f"pdftotext could not run: {e}")
    if out.returncode != 0:
        return _Refusal(f"pdftotext exited {out.returncode}")
    return out.stdout.decode("utf-8", "replace")


def render_ocr(pdf: str, page: int, dpi: int = 300, region=None):
    """Render the page and OCR it. Returns (text or _Refusal or None, png path)."""
    cache = os.path.expanduser("~/.cache/pdf-form-proof")
    os.makedirs(cache, exist_ok=True)
    for old in glob.glob(os.path.join(cache, "render_*")):
        shutil.rmtree(old, ignore_errors=True)
    d = tempfile.mkdtemp(prefix="render_", dir=cache)
    stem = os.path.join(d, "p")
    rr = subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(page), "-l", str(page), pdf, stem],
                        capture_output=True, timeout=300)
    pngs = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".png")]
    if rr.returncode != 0:
        e = next((x for x in rr.stderr.decode("utf-8", "replace").splitlines() if x.strip()), "(no stderr)")
        return _Refusal(f"pdftoppm exited {rr.returncode}; the page was never rendered: {e.strip()[:120]}"), None
    if not pngs:
        return _Refusal(f"pdftoppm exited 0 but produced no image for page {page}"), None
    png = pngs[0]
    if region:
        try:
            from PIL import Image
        except Exception:
            return None, png  # caller refuses: no silent whole-page fallback
        img = Image.open(png)
        W, H = img.size
        x0, y0, x1, y1 = region
        px, py = 0.02, 0.01  # pad the crop so a boundary glyph keeps its whole body
        img = img.crop((max(0, int((x0 - px) * W)), max(0, int((y0 - py) * H)),
                        min(W, int((x1 + px) * W)), min(H, int((y1 + py) * H))))
        img.save(png)
    # The tesseract binary is used directly; a Python wrapper was observed to
    # return an empty string with no error on one interpreter.
    r = subprocess.run(["tesseract", png, "stdout"], capture_output=True, timeout=300)
    if r.returncode != 0:
        e = next((x for x in r.stderr.decode("utf-8", "replace").splitlines() if x.strip()), "(no stderr)")
        return _Refusal(f"tesseract exited {r.returncode}; OCR never read the page: {e.strip()[:120]}"), png
    return r.stdout.decode("utf-8", "replace"), png


def parse_region(region):
    if region is None or (isinstance(region, str) and region.strip().lower() in ("", "page")):
        return None
    if isinstance(region, str):
        region = [float(x) for x in region.split(",")]
    region = tuple(float(x) for x in region)
    if len(region) != 4 or not all(0.0 <= v <= 1.0 for v in region):
        raise ValueError("region must be four fractions x0,y0,x1,y1 between 0 and 1")
    return region


def check_legibility(pdf: str, require: list[str] | None = None, page: int = 1,
                     region=None, auto: bool = True, dpi: int = 300,
                     ignore_words: list[str] | None = None,
                     keep_render: str | None = None) -> dict[str, Any]:
    """Return {"verdict": PASS|REFUSE, "rows": [...], ...}. Exit-code mapping
    for the CLI: PASS 0, a finding against the document 1, a tool failure 2."""
    require = [r for r in (require or []) if r and r.strip()]
    if not os.path.isfile(pdf):
        return {"verdict": "REFUSE", "kind": "usage", "reason": f"no such file: {pdf}"}
    missing = _missing_binaries()
    if missing:
        return {"verdict": "REFUSE", "kind": "tool_failure",
                "reason": "missing required programs: " + ", ".join(missing),
                "install": "macOS: brew install poppler tesseract; Debian/Ubuntu: apt install poppler-utils tesseract-ocr"}
    try:
        reg = parse_region(region)
    except Exception as e:
        return {"verdict": "REFUSE", "kind": "usage", "reason": str(e)}

    tl_raw = text_layer(pdf, page)
    if isinstance(tl_raw, _Refusal):
        return {"verdict": "REFUSE", "kind": "tool_failure", "reason": tl_raw.reason}
    ocr_raw, png = render_ocr(pdf, page, dpi, reg)
    if isinstance(ocr_raw, _Refusal):
        return {"verdict": "REFUSE", "kind": "tool_failure", "reason": ocr_raw.reason,
                "note": "no legibility finding is made; an empty OCR result is not evidence"}
    if ocr_raw is None:
        return {"verdict": "REFUSE", "kind": "tool_failure",
                "reason": "region scoping needs Pillow (pip install pillow), or pass region='page'"}

    auto_words: list[str] = []
    if auto and reg:
        got = region_words(pdf, page, reg)
        if got is None:
            return {"verdict": "REFUSE", "kind": "tool_failure",
                    "reason": "could not read the text layer to derive the auto requirement set"}
        skip = {w.lower() for w in (ignore_words or [])}
        auto_words = [w for w in got if w.lower() not in skip]

    checks = [(w, "auto") for w in auto_words] + [(w, "required") for w in require]
    if not checks:
        return {"verdict": "REFUSE", "kind": "usage",
                "reason": "nothing to check: give values in 'require', or a region with text for auto mode"}

    tl, ocr = norm(tl_raw), norm(ocr_raw)
    rows, refuse = [], False
    for want, origin in checks:
        n = norm(want)
        if not n:
            continue
        in_text, in_ocr = n in tl, n in ocr
        if in_text and in_ocr:
            v = "VISIBLE"
        elif in_text:
            v = "INVISIBLE"; refuse = True
        elif in_ocr:
            v = "RENDER-ONLY"; refuse = True
        else:
            v = "MISSING"; refuse = True
        rows.append({"value": want, "source": origin, "in_text_layer": in_text,
                     "in_render": in_ocr, "verdict": v})
    kept = None
    if keep_render and png:
        try:
            shutil.copy2(png, keep_render)
            kept = keep_render
        except Exception:
            kept = None
    return {
        "verdict": "REFUSE" if refuse else "PASS",
        "kind": "finding" if refuse else "pass",
        "file": pdf, "page": page, "dpi": dpi,
        "scope": f"region {reg}" if reg else "whole page",
        "rows": rows,
        "auto_values": len(auto_words), "required_values": len(require),
        "render_kept": kept,
        "limit": "legibility only; says nothing about whether values are correct",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prove filled PDF values are visible on the rendered page.")
    ap.add_argument("pdf")
    ap.add_argument("--require", action="append", default=[], help="a value that must be legible")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--region", default="page", help="x0,y0,x1,y1 as page fractions, or 'page'")
    ap.add_argument("--no-auto", action="store_true", help="only check --require values")
    ap.add_argument("--keep-render", default=None, help="copy the rendered PNG here")
    a = ap.parse_args(argv)
    res = check_legibility(a.pdf, a.require, a.page, a.region, not a.no_auto, a.dpi,
                           keep_render=a.keep_render)
    print(f"PDF FORM PROOF - legibility check  verdict: {res['verdict']}")
    for k in ("reason", "install", "note"):
        if k in res:
            print(f"  {k}: {res[k]}")
    if res.get("rows"):
        print(f"  file: {os.path.basename(a.pdf)}  page {res['page']} at {res['dpi']} dpi  scope: {res['scope']}")
        w = max(len(r["value"]) for r in res["rows"])
        print(f"  {'VALUE':<{w}}  SOURCE    TEXT  RENDER  VERDICT")
        for r in res["rows"]:
            print(f"  {r['value']:<{w}}  {r['source']:<8}  {'yes' if r['in_text_layer'] else 'no':<4}  "
                  f"{'yes' if r['in_render'] else 'no':<6}  {r['verdict']}")
        print(f"  limit: {res['limit']}")
    if res["verdict"] == "PASS":
        return 0
    return 1 if res.get("kind") == "finding" else 2


if __name__ == "__main__":
    sys.exit(main())
