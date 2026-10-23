"""Smoke test. Put any fillable AcroForm PDF at tests/fixtures/form.pdf
(the public IRS W-9 works: https://www.irs.gov/pub/irs-pdf/fw9.pdf) and run
`python -m pytest tests`. The test is skipped when the fixture is absent."""

import os
import shutil

import pytest

from pdf_form_proof.fill import fill_form, inspect_form
from pdf_form_proof.legibility import check_legibility

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "form.pdf")


@pytest.mark.skipif(not os.path.isfile(FIX), reason="no fixture PDF")
def test_fill_refuse_and_verify(tmp_path):
    info = inspect_form(FIX)
    assert info["kind"] == "acroform"
    text = [f["name"] for f in info["fields"] if f["type"] == "text"]
    assert text
    bad = fill_form(FIX, str(tmp_path / "bad.pdf"), {"no.such.field": "x"})
    assert bad["status"] == "refused" and not (tmp_path / "bad.pdf").exists()
    out = str(tmp_path / "out.pdf")
    res = fill_form(FIX, out, {text[0]: "Sample Community Food Bank"})
    assert res["status"] == "ok"
    again = fill_form(FIX, out, {text[0]: "x"})
    assert again["status"] == "refused"
    if all(shutil.which(b) for b in ("pdftotext", "pdftoppm", "tesseract")):
        v = check_legibility(out, ["Sample Community Food Bank"])
        assert v["verdict"] == "PASS"
        cache = os.path.expanduser("~/.cache/pdf-form-proof")
        left = [f for _, _, fs in os.walk(cache) for f in fs if f.endswith(".png")]
        assert left == [], left
