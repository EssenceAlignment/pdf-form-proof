"""pdf-form-proof remote: a stateless Streamable-HTTP MCP server for ChatGPT and other
hosted clients (0.1.6).

INPUT. Every tool takes ONE file object, the shape ChatGPT hands a tool for an attached
file (declared in _meta["openai/fileParams"]): {download_url, file_id, mime_type?,
file_name?}. The server fetches download_url over HTTPS into memory, checks the bytes
start with %PDF and are under PFP_MAX_PDF_BYTES, and never keeps the URL, the id, or the
bytes after the call returns. Any client can use the same shape with a public HTTPS URL.

OUTPUT. Tools return a proof receipt (hashes, field table, verdicts), never a file by
default. pdf_form_fill runs the read-back and the rendered-page check inside the same
call, so one turn produces the fill and its proof. include_base64=true adds the filled
PDF as base64 for API clients that can save it; ChatGPT chat has no file-return channel,
so the flag stays off there.

MEMORY. inspect, fill and sha256 run on io.BytesIO. verify (and the check inside fill)
writes the bytes to a temporary directory for poppler and tesseract and deletes it in a
finally block before the response is built. No logging of request content, URLs or ids.
No timestamps, request ids or telemetry in any response.

Run:  PORT=8080 python server_remote.py        (Cloud Run reads PORT)
MCP endpoint: POST http://<host>:<port>/mcp     (stateless, JSON responses)
Sample forms (no personal fields) are served at /samples/<name>.pdf for review test cases.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import sys
import tempfile
import urllib.request
from typing import Any

import anyio

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader, PdfWriter

# Vendored package lives in ./src when run from the repo; installed otherwise.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from pdf_form_proof import __version__  # noqa: E402
from pdf_form_proof._acroform import _apply_checkbox_states, _btn_field_names  # noqa: E402
from pdf_form_proof.fill import _button_on_states  # noqa: E402
from pdf_form_proof.legibility import check_legibility  # noqa: E402

MAX_BYTES = int(os.environ.get("PFP_MAX_PDF_BYTES", str(25 * 1024 * 1024)))
FETCH_TIMEOUT = int(os.environ.get("PFP_FETCH_TIMEOUT", "30"))
SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
FILE_META = {"openai/fileParams": ["file"]}

mcp = FastMCP(
    "pdf-form-proof",
    instructions=(
        "Fill fillable (AcroForm) PDF forms and prove the result. Every tool takes the "
        "user's attached PDF as its 'file' input. Workflow: 1) pdf_form_inspect for exact "
        "field names and checkbox on-states; 2) pdf_form_fill with those names: unknown "
        "names are refused and nothing is produced; the fill is verified on the rendered "
        "page in the same call and a proof receipt (hashes and a per-field table) comes "
        "back; 3) pdf_form_verify checks an already filled PDF the user attaches. Report "
        "REFUSE verdicts as-is. This tool fills forms; it does not give legal, tax, or "
        "benefits advice. Do not put government identifiers, payment card numbers or "
        "health information into a fill."
    ),
    stateless_http=True,
    json_response=True,
    host=os.environ.get("HOST", "0.0.0.0"),
    port=int(os.environ.get("PORT", "8080")),
)
# Report the plugin's own version, not the MCP library's.
mcp._mcp_server.version = __version__


class OpenAIFile(BaseModel):
    """A file the client attached. ChatGPT always sends download_url and file_id."""

    model_config = ConfigDict(extra="forbid")
    download_url: str = Field(description="Temporary HTTPS URL to fetch the file from.")
    file_id: str = Field(description="Client-side file identifier.")
    mime_type: str | None = Field(default=None, description="MIME type, if known.")
    file_name: str | None = Field(default=None, description="Original file name, if known.")


def _fetch(file: OpenAIFile) -> bytes | dict[str, Any]:
    url = (file.download_url or "").strip()
    allowed = ("https://",) + (("http://",) if os.environ.get("PFP_ALLOW_HTTP") == "1" else ())
    if not url.lower().startswith(allowed):
        return {"status": "refused", "reason": "download_url must be https"}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pdf-form-proof/" + __version__})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES + 1)
    except Exception:
        return {"status": "refused", "reason": "the attached file could not be fetched"}
    if len(raw) > MAX_BYTES:
        return {"status": "refused", "reason": f"PDF larger than {MAX_BYTES} bytes"}
    if not raw.startswith(b"%PDF"):
        return {"status": "refused", "reason": "the attached file is not a PDF"}
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
    fields = acro.get("/Fields")
    if fields and len(fields) > 0:
        # Hybrid XFA+AcroForm: fill the AcroForm fields; finalize strips /XFA.
        return "acroform"
    return "xfa" if "/XFA" in acro else "none"


def _verify_bytes(raw: bytes, values: list[str], page: int = 1, region: str = "page",
                  auto: bool = True, dpi: int = 300) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pfp-remote-") as d:
        path = os.path.join(d, "in.pdf")
        try:
            with open(path, "wb") as f:
                f.write(raw)
            res = check_legibility(path, require=values, page=page, region=region,
                                   auto=auto, dpi=dpi)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    for k in ("pdf", "pdf_path", "file", "render_kept"):
        res.pop(k, None)
    return res


@mcp.tool(
    title="Inspect PDF form fields",
    annotations=ToolAnnotations(title="Inspect PDF form fields", readOnlyHint=True,
                                destructiveHint=False, openWorldHint=False),
    meta=FILE_META,
)
async def pdf_form_inspect(file: OpenAIFile) -> dict[str, Any]:
    """List every fillable field in the attached PDF: exact name, type, current value,
    required flag, and for checkboxes/radios the on-state values. Nothing is stored."""
    return await anyio.to_thread.run_sync(_pdf_form_inspect_sync, file)


def _pdf_form_inspect_sync(file: OpenAIFile) -> dict[str, Any]:
    raw = _fetch(file)
    if isinstance(raw, dict):
        return raw
    kind = _kind(raw)
    sha = hashlib.sha256(raw).hexdigest()
    if kind != "acroform":
        return {"status": "refused" if kind == "unreadable" else "ok", "kind": kind,
                "field_count": 0, "fields": [], "sha256": sha,
                "note": {"xfa": "XFA-only form: this tool does not fill it.",
                         "none": "No fillable (AcroForm) fields: this is a flat PDF and cannot be filled.",
                         "unreadable": "The file could not be read as a PDF."}[kind]}
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
            "type": {"/Tx": "text", "/Btn": "checkbox", "/Ch": "choice",
                     "/Sig": "signature"}.get(str(ftype), str(ftype)),
            "value": None if fobj.get("/V") is None else str(fobj.get("/V")),
            "required": bool(fobj.get("/Ff") and int(fobj.get("/Ff", 0)) & 2),
        }
        if str(ftype) == "/Btn":
            entry["on_states"] = ["/" + s for s in on_states.get(name, [])]
        if str(ftype) == "/Ch" and fobj.get("/Opt") is not None:
            entry["options"] = [str(o) for o in fobj.get("/Opt")]
        out.append(entry)
    return {"status": "ok", "kind": kind, "field_count": len(out), "fields": out, "sha256": sha}


@mcp.tool(
    title="Fill PDF form and prove it",
    annotations=ToolAnnotations(title="Fill PDF form and prove it", readOnlyHint=True,
                                destructiveHint=False, idempotentHint=True, openWorldHint=False),
    meta=FILE_META,
)
async def pdf_form_fill(file: OpenAIFile, values: dict[str, Any], strict: bool = True,
                  finalize: bool = True, include_base64: bool = False) -> dict[str, Any]:
    """Fill the attached AcroForm PDF in memory by exact field name, then prove the result:
    the response is a receipt with the input and output SHA-256, every field written, a
    read-back of each value, and a rendered-page check (VISIBLE / INVISIBLE / MISSING per
    text value). The attached file is never modified. strict=true refuses the whole fill
    if any field name is unknown (nothing is produced). include_base64=true adds the filled
    PDF as base64 for clients that can save a file; leave it false in chat."""
    return await anyio.to_thread.run_sync(_pdf_form_fill_sync, file, values, strict, finalize, include_base64)


def _pdf_form_fill_sync(file: OpenAIFile, values: dict[str, Any], strict: bool = True,
                  finalize: bool = True, include_base64: bool = False) -> dict[str, Any]:
    raw = _fetch(file)
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
            if isinstance(val, bool):
                val = "/Yes" if val else ""
            elif isinstance(val, str) and val.strip().lower() in ("off", "/off", "false", "no", ""):
                val = ""
            elif isinstance(val, str) and val.strip().lower() in ("on", "true", "yes", "checked"):
                val = "/Yes"
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
    # A checkbox asked for as /Yes uses the field's real on-state when that differs.
    on_states = _button_on_states(reader)
    on_map: dict[str, str] = {}
    for k, v in checkbox_values.items():
        if isinstance(v, str) and v.startswith("/"):
            want = v.lstrip("/")
            states = on_states.get(k, [])
            on_map[k] = want if (want in states or not states) else states[0]
            checkbox_values[k] = "/" + on_map[k]
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
    read_back = {k: str(back.get(k, {}).get("/V", "")) for k in text_values}
    mismatched = [k for k, v in text_values.items() if read_back[k] != v]
    want_visible = [v for v in text_values.values() if v.strip()]
    try:
        visual = _verify_bytes(out, want_visible) if want_visible else {"verdict": "SKIP", "reason": "no text values to check"}
    except Exception:
        visual = {"verdict": "REFUSE", "kind": "tool_failure", "reason": "rendered-page check did not run"}
    status = "ok" if (not mismatched and visual.get("verdict") in ("PASS", "SKIP")) else (
        "mismatch" if mismatched else "check_failed")
    receipt: dict[str, Any] = {
        "status": status,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "output_sha256": hashlib.sha256(out).hexdigest(),
        "output_bytes": len(out),
        "fields_written": {**read_back, **{k: str(v) for k, v in checkbox_values.items()}},
        "checkbox_results": cb,
        "read_back_mismatches": mismatched,
        "rendered_page_check": visual,
        "pages_touched": pages_touched,
        "finalize": finalize_changes,
        "limit": "This proves the values show on the page, not that they are correct.",
    }
    if include_base64:
        receipt["output_pdf_base64"] = base64.b64encode(out).decode("ascii")
    return receipt


@mcp.tool(
    title="Verify filled values are visible",
    annotations=ToolAnnotations(title="Verify filled values are visible", readOnlyHint=True,
                                destructiveHint=False, openWorldHint=False),
    meta=FILE_META,
)
async def pdf_form_verify(file: OpenAIFile, values: list[str] | None = None, page: int = 1,
                    region: str = "page", auto: bool = True, dpi: int = 300) -> dict[str, Any]:
    """Render one page of the attached PDF, OCR it, and compare with the PDF text layer.
    Each value gets a verdict: VISIBLE, INVISIBLE, RENDER-ONLY, or MISSING. A tool failure
    returns REFUSE and makes no finding. For a blank form a value you expect to see is
    reported MISSING."""
    return await anyio.to_thread.run_sync(_pdf_form_verify_sync, file, values, page, region, auto, dpi)


def _pdf_form_verify_sync(file: OpenAIFile, values: list[str] | None = None, page: int = 1,
                    region: str = "page", auto: bool = True, dpi: int = 300) -> dict[str, Any]:
    raw = _fetch(file)
    if isinstance(raw, dict):
        return {"verdict": "REFUSE", "kind": "usage", "reason": raw["reason"]}
    try:
        res = _verify_bytes(raw, values or [], page=page, region=region, auto=auto, dpi=dpi)
    except Exception:
        return {"verdict": "REFUSE", "kind": "tool_failure", "reason": "rendered-page check did not run"}
    res["sha256"] = hashlib.sha256(raw).hexdigest()
    return res


@mcp.tool(
    title="SHA-256 of a PDF",
    annotations=ToolAnnotations(title="SHA-256 of a PDF", readOnlyHint=True,
                                destructiveHint=False, openWorldHint=False),
    meta=FILE_META,
)
async def pdf_sha256(file: OpenAIFile) -> dict[str, Any]:
    """SHA-256 fingerprint and byte size of the attached PDF, for a record of exactly what
    was received or produced."""
    return await anyio.to_thread.run_sync(_pdf_sha256_sync, file)


def _pdf_sha256_sync(file: OpenAIFile) -> dict[str, Any]:
    raw = _fetch(file)
    if isinstance(raw, dict):
        return raw
    return {"status": "ok", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _inline_file_schema() -> None:
    """ChatGPT requires the file field to be a plain object with the four properties,
    not a $ref into $defs. Rewrite each tool's input schema so 'file' is inline."""
    for tool in mcp._tool_manager.list_tools():
        params = tool.parameters
        defs = params.get("$defs", {})
        props = params.get("properties", {})
        spec = props.get("file")
        if not spec or "$ref" not in spec:
            continue
        name = spec["$ref"].rsplit("/", 1)[-1]
        if name in defs:
            inline = dict(defs[name])
            inline.pop("title", None)
            inline["additionalProperties"] = False
            props["file"] = inline
        if not any("$ref" in str(v) for v in props.values()):
            params.pop("$defs", None)


_inline_file_schema()


# OpenAI plugin directory domain verification: serve the exact challenge token as plain text.
@mcp.custom_route("/.well-known/openai-apps-challenge", methods=["GET"])
async def openai_apps_challenge(request):  # noqa: ARG001
    from starlette.responses import PlainTextResponse
    token = os.environ.get("OPENAI_APPS_CHALLENGE", "")
    return PlainTextResponse(token, status_code=200 if token else 404)


# Public sample forms with no personal fields, for review test cases.
@mcp.custom_route("/samples/{name}", methods=["GET"])
async def sample_pdf(request):
    from starlette.responses import FileResponse, PlainTextResponse
    name = os.path.basename(request.path_params.get("name", ""))
    path = os.path.join(SAMPLES_DIR, name)
    if not name.endswith(".pdf") or not os.path.isfile(path):
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(path, media_type="application/pdf", filename=name)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request):  # noqa: ARG001
    from starlette.responses import PlainTextResponse
    return PlainTextResponse("ok " + __version__)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
