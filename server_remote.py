"""pdf-form-proof remote: a stateless Streamable-HTTP MCP wrapper.

Every tool takes the PDF as base64 in the request and returns results (and the
filled PDF) as base64 in the response. No tool accepts or returns a file path.

What stays in memory: inspect, fill, and sha256 run entirely on io.BytesIO.
Stated limit: verify shells out to poppler (pdftotext, pdftoppm) and tesseract,
which read only files, so verify writes the bytes to a temporary directory and
deletes that directory in a finally block before the response is built. Nothing
is retained after the call returns. No logging of request content.

Run:  PORT=8080 python server_remote.py        (Cloud Run reads PORT)
MCP endpoint: POST http://<host>:<port>/mcp     (stateless, JSON responses)
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import sys
import tempfile
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pypdf import PdfReader, PdfWriter

# Vendored package lives in ./src when run from the repo; installed otherwise.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from pdf_form_proof import __version__  # noqa: E402
from pdf_form_proof._acroform import _apply_checkbox_states, _btn_field_names  # noqa: E402
from pdf_form_proof.fill import _button_on_states  # noqa: E402
from pdf_form_proof.legibility import check_legibility  # noqa: E402

MAX_BYTES = int(os.environ.get("PFP_MAX_PDF_BYTES", str(25 * 1024 * 1024)))

mcp = FastMCP(
    "pdf-form-proof",
    instructions=(
        "Fill fillable (AcroForm) PDF forms and prove the result, over HTTP. Send the PDF "
        "as base64. Workflow: 1) pdf_form_inspect for exact field names and checkbox "
        "on-states; 2) pdf_form_fill with those names (unknown names are refused, nothing "
        "is produced); 3) pdf_form_verify on the returned PDF to confirm each value is "
        "visible on the rendered page. Report REFUSE verdicts as-is. This tool fills forms; "
        "it does not give legal, tax, or benefits advice."
    ),
    stateless_http=True,
    json_response=True,
    host=os.environ.get("HOST", "0.0.0.0"),
    port=int(os.environ.get("PORT", "8080")),
)


def _decode(pdf_base64: str) -> bytes | dict[str, Any]:
    try:
        raw = base64.b64decode(pdf_base64, validate=True)
    except Exception:
        return {"status": "refused", "reason": "pdf_base64 is not valid base64"}
    if not raw.startswith(b"%PDF"):
        return {"status": "refused", "reason": "payload does not start with %PDF"}
    if len(raw) > MAX_BYTES:
        return {"status": "refused", "reason": f"PDF larger than {MAX_BYTES} bytes"}
    return raw


def _kind(raw: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception:
        return "unreadable"
    acro = reader.trailer["/Root"].get("/AcroForm")
    if acro is None:
        return "none"
    acro = acro.get_object()
    if "/XFA" in acro:
        return "xfa"
    fields = acro.get("/Fields")
    return "acroform" if fields and len(fields) > 0 else "none"


@mcp.tool(
    title="Inspect PDF form fields",
    annotations=ToolAnnotations(title="Inspect PDF form fields", readOnlyHint=True,
                                destructiveHint=False, openWorldHint=False),
)
def pdf_form_inspect(pdf_base64: str) -> dict[str, Any]:
    """List every fillable field in a base64 PDF: exact name, type, current value,
    required flag, and for checkboxes/radios the on-state values. Nothing is stored."""
    raw = _decode(pdf_base64)
    if isinstance(raw, dict):
        return raw
    kind = _kind(raw)
    if kind != "acroform":
        return {"kind": kind, "field_count": 0, "fields": [],
                "note": "XFA-only form: not filled." if kind == "xfa" else "No AcroForm fields found."}
    reader = PdfReader(io.BytesIO(raw))
    fields = reader.get_fields() or {}
    on_states = _button_on_states(reader)
    out = []
    for name, fobj in fields.items():
        ftype = fobj.get("/FT")
        if ftype is None:
            continue
        entry = {
            "name": name,
            "type": {"/Tx": "text", "/Btn": "button", "/Ch": "choice",
                     "/Sig": "signature"}.get(str(ftype), str(ftype)),
            "value": None if fobj.get("/V") is None else str(fobj.get("/V")),
            "required": bool(fobj.get("/Ff") and int(fobj.get("/Ff", 0)) & 2),
        }
        if str(ftype) == "/Btn":
            entry["on_states"] = ["/" + s for s in on_states.get(name, [])]
        if str(ftype) == "/Ch" and fobj.get("/Opt") is not None:
            entry["options"] = [str(o) for o in fobj.get("/Opt")]
        out.append(entry)
    return {"kind": kind, "field_count": len(out), "fields": out,
            "sha256": hashlib.sha256(raw).hexdigest()}


@mcp.tool(
    title="Fill PDF form",
    annotations=ToolAnnotations(title="Fill PDF form", readOnlyHint=True,
                                destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def pdf_form_fill(pdf_base64: str, values: dict[str, Any], strict: bool = True,
                  finalize: bool = False) -> dict[str, Any]:
    """Fill an AcroForm PDF in memory and return the NEW PDF as base64 plus its SHA-256
    and a read-back of every text value. The input is never modified. strict=true
    refuses the whole fill if any field name is unknown (nothing is produced)."""
    raw = _decode(pdf_base64)
    if isinstance(raw, dict):
        return raw
    kind = _kind(raw)
    if kind != "acroform":
        return {"status": "refused", "reason": f"form kind is {kind!r}; only AcroForm forms are filled"}
    reader = PdfReader(io.BytesIO(raw))
    known = set((reader.get_fields() or {}).keys())
    unknown = sorted(k for k in values if k not in known)
    if unknown and strict:
        return {"status": "refused", "reason": "field names not in this form; nothing produced",
                "unknown_fields": unknown, "hint": "run pdf_form_inspect and copy names exactly"}
    writer = PdfWriter(clone_from=reader)
    btn_names = _btn_field_names(reader)
    checkbox_values: dict = {}
    text_values: dict = {}
    for name, val in values.items():
        if name not in known:
            continue
        if name in btn_names:
            if isinstance(val, str) and val.strip().lower() in ("off", "/off"):
                val = ""
            checkbox_values[name] = val
        else:
            text_values[name] = "" if val is None else str(val)
    pages_touched = 0
    if text_values:
        for page in writer.pages:
            try:
                writer.update_page_form_field_values(page, text_values)
                pages_touched += 1
            except Exception:
                continue
    on_map = {k: str(v).lstrip("/") for k, v in checkbox_values.items()
              if isinstance(v, str) and v.startswith("/")}
    cb = _apply_checkbox_states(writer, checkbox_values, on_map)
    finalize_changes: list[str] = []
    if finalize:
        try:
            acro = writer._root_object.get("/AcroForm")
            if acro is not None:
                acro = acro.get_object() if hasattr(acro, "get_object") else acro
                if "/XFA" in acro:
                    del acro["/XFA"]
                    finalize_changes.append("removed /XFA")
                if "/NeedAppearances" in acro:
                    del acro["/NeedAppearances"]
                    finalize_changes.append("removed /NeedAppearances")
        except Exception as e:
            finalize_changes.append(f"finalize error: {e}")
    buf = io.BytesIO()
    writer.write(buf)
    out = buf.getvalue()
    back = PdfReader(io.BytesIO(out)).get_fields() or {}
    mismatched = [k for k, v in text_values.items()
                  if str(back.get(k, {}).get("/V", "")) != v]
    return {
        "status": "ok" if not mismatched else "mismatch",
        "output_pdf_base64": base64.b64encode(out).decode("ascii"),
        "output_sha256": hashlib.sha256(out).hexdigest(),
        "output_bytes": len(out),
        "text_fields_written": sorted(text_values),
        "checkbox_results": cb,
        "skipped_unknown": unknown,
        "pages_touched": pages_touched,
        "finalize": finalize_changes,
        "read_back_mismatches": mismatched,
    }


@mcp.tool(
    title="Verify filled values are visible",
    annotations=ToolAnnotations(title="Verify filled values are visible", readOnlyHint=True,
                                destructiveHint=False, openWorldHint=False),
)
def pdf_form_verify(pdf_base64: str, values: list[str] | None = None, page: int = 1,
                    region: str = "page", auto: bool = True, dpi: int = 300) -> dict[str, Any]:
    """Render one page, OCR it, and compare with the PDF text layer. Each value gets a
    verdict: VISIBLE, INVISIBLE, RENDER-ONLY, or MISSING. A tool failure returns REFUSE
    and makes no finding. Stated limit: the bytes are written to a temporary directory
    for poppler and tesseract and that directory is deleted before this returns."""
    raw = _decode(pdf_base64)
    if isinstance(raw, dict):
        return {"verdict": "REFUSE", "kind": "usage", "reason": raw["reason"]}
    with tempfile.TemporaryDirectory(prefix="pfp-remote-") as d:
        path = os.path.join(d, "in.pdf")
        try:
            with open(path, "wb") as f:
                f.write(raw)
            res = check_legibility(path, require=values or [], page=page, region=region,
                                   auto=auto, dpi=dpi)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    res.pop("pdf", None)
    res.pop("pdf_path", None)
    res["sha256"] = hashlib.sha256(raw).hexdigest()
    return res


@mcp.tool(
    title="SHA-256 of a PDF",
    annotations=ToolAnnotations(title="SHA-256 of a PDF", readOnlyHint=True,
                                destructiveHint=False, openWorldHint=False),
)
def pdf_sha256(pdf_base64: str) -> dict[str, Any]:
    """SHA-256 fingerprint of the decoded PDF bytes, for a record of exactly what was
    produced or received."""
    raw = _decode(pdf_base64)
    if isinstance(raw, dict):
        return raw
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "server_version": __version__}


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
