"""Runs the 5 positive and 3 negative directory-submission cases against the remote
wrapper's tool functions in-process. Uses tests/fixtures/form.pdf when present,
otherwise builds a minimal one-field AcroForm PDF in memory."""

import base64
import glob
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import server_remote as sr  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "form.pdf")
FIELD_FALLBACK = "name"


def _minimal_acroform() -> bytes:
    """One page, one text field named 'name', Helvetica, NeedAppearances on."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] /DA (/Helv 0 Tf 0 g) /DR << /Font << /Helv 6 0 R >> >> /NeedAppearances true >> >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Annots [5 0 R] /Resources << /Font << /Helv 6 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length 44 >>\nstream\nBT /Helv 12 Tf 72 720 Td (Name:) Tj ET\nendstream",
        b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (name) /Rect [140 710 500 735] /F 4 /P 3 0 R /DA (/Helv 12 Tf 0 g) /V () >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode()
    for o in offsets:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _pdf_b64() -> str:
    if os.path.isfile(FIX):
        with open(FIX, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return base64.b64encode(_minimal_acroform()).decode()


def _tmpdirs():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "pfp-remote-*")))


def run() -> int:
    receipts = []
    ok = True

    def rec(cid, passed, detail):
        nonlocal ok
        ok = ok and passed
        receipts.append({"id": cid, "result": "PASS" if passed else "FAIL", "detail": detail})

    pdf = _pdf_b64()
    before = _tmpdirs()

    info = sr.pdf_form_inspect(pdf)
    text = [f["name"] for f in info.get("fields", []) if f["type"] == "text"]
    rec("P1", info.get("kind") == "acroform" and info.get("field_count", 0) >= 1 and bool(text),
        f"kind={info.get('kind')} field_count={info.get('field_count')}")

    val = "Sample Community Food Bank"
    filled = sr.pdf_form_fill(pdf, {text[0] if text else FIELD_FALLBACK: val})
    rec("P2", filled.get("status") == "ok" and len(filled.get("output_sha256", "")) == 64
        and filled.get("read_back_mismatches") == [],
        f"status={filled.get('status')} sha256={filled.get('output_sha256','')[:16]} bytes={filled.get('output_bytes')}")

    out_b64 = filled.get("output_pdf_base64", "")
    tools = all(shutil.which(b) for b in ("pdftotext", "pdftoppm", "tesseract"))
    v = sr.pdf_form_verify(out_b64, [val]) if out_b64 else {"verdict": "REFUSE"}
    if tools:
        rec("P3", v.get("verdict") == "PASS", f"verdict={v.get('verdict')} reason={v.get('reason','')}"[:160])
    else:
        rec("P3", v.get("verdict") == "REFUSE" and v.get("kind") == "tool_failure",
            f"no poppler/tesseract here; verdict={v.get('verdict')} kind={v.get('kind')}")

    h = sr.pdf_sha256(out_b64) if out_b64 else {}
    rec("P4", h.get("sha256") == filled.get("output_sha256") and h.get("bytes") == filled.get("output_bytes"),
        f"sha256 match={h.get('sha256') == filled.get('output_sha256')}")

    fin = sr.pdf_form_fill(pdf, {}, finalize=True)
    rec("P5", fin.get("status") == "ok" and isinstance(fin.get("finalize"), list),
        f"status={fin.get('status')} finalize={fin.get('finalize')}")

    n1 = sr.pdf_form_fill(pdf, {"no.such.field": "x"})
    rec("N1", n1.get("status") == "refused" and n1.get("unknown_fields") == ["no.such.field"]
        and "output_pdf_base64" not in n1, f"status={n1.get('status')} unknown={n1.get('unknown_fields')}")

    n2 = sr.pdf_form_inspect("not base64!!")
    rec("N2", n2.get("status") == "refused" and "base64" in n2.get("reason", ""), f"reason={n2.get('reason')}")

    n3 = sr.pdf_form_verify(base64.b64encode(b"hello world").decode())
    rec("N3", n3.get("verdict") == "REFUSE" and n3.get("kind") == "usage", f"verdict={n3.get('verdict')} kind={n3.get('kind')}")

    after = _tmpdirs()
    rec("S1", after - before == set(), f"leftover temp dirs: {sorted(after - before)}")

    print(json.dumps({"overall": "PASS" if ok else "FAIL", "cases": receipts}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
