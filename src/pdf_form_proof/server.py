"""MCP server (stdio) for pdf-form-proof.

Tools:
  pdf_form_inspect  - list every fillable field, its type, and checkbox on-states
  pdf_form_fill     - fill by exact field name; refuses unknown names; never overwrites
  pdf_form_verify   - prove the values are visible on the rendered page (OCR vs text layer)
  pdf_sha256        - SHA-256 of a file, for a record of exactly what was produced

Everything runs locally. No network calls, no API keys, no telemetry.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import __version__
from .fill import fill_form, inspect_form, sha256_file
from .legibility import check_legibility

mcp = FastMCP(
    "pdf-form-proof",
    instructions=(
        "Fill fillable (AcroForm) PDF forms and prove the result. Workflow: "
        "1) pdf_form_inspect to get exact field names and checkbox on-states; "
        "2) pdf_form_fill with those exact names (unknown names are refused, nothing written); "
        "3) pdf_form_verify with the filled values to confirm each one is visible on the "
        "rendered page, not only stored in the file. Report REFUSE verdicts to the user as-is. "
        "This tool fills forms; it does not give legal, tax, or benefits advice."
    ),
)


@mcp.tool(
    title="Inspect PDF form fields",
    annotations=ToolAnnotations(title="Inspect PDF form fields", readOnlyHint=True,
                                destructiveHint=False, openWorldHint=False),
)
def pdf_form_inspect(pdf_path: str) -> dict[str, Any]:
    """List every fillable field in a PDF: exact name, type (text, button, choice,
    signature), current value, required flag, and for checkboxes/radios the on-state
    values that check them. Use the names exactly as returned when filling."""
    return inspect_form(pdf_path)


@mcp.tool(
    title="Fill PDF form",
    annotations=ToolAnnotations(title="Fill PDF form", readOnlyHint=False,
                                destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
def pdf_form_fill(source_pdf_path: str, output_path: str, values: dict[str, Any],
                  strict: bool = True, finalize: bool = False) -> dict[str, Any]:
    """Fill an AcroForm PDF and write a NEW file (the source is never modified and an
    existing output is never overwritten). values maps exact field names to values:
    strings for text fields; an on-state such as "/1" or "/Yes" (or true) to check a box,
    "" or false to clear it. strict=true refuses the whole fill if any name is unknown.
    finalize=true removes the XFA packet and NeedAppearances flag so viewers render the
    values from their own appearance streams (fields stay editable; this is not flattening).
    Returns the output SHA-256 and a read-back of every text value."""
    return fill_form(source_pdf_path, output_path, values, strict=strict, finalize=finalize)


@mcp.tool(
    title="Verify filled values are visible",
    annotations=ToolAnnotations(title="Verify filled values are visible", readOnlyHint=True,
                                destructiveHint=False, openWorldHint=False),
)
def pdf_form_verify(pdf_path: str, values: list[str] | None = None, page: int = 1,
                    region: str = "page", auto: bool = True, dpi: int = 300) -> dict[str, Any]:
    """Render one page, OCR it, and compare with the PDF text layer. Each value gets a
    verdict: VISIBLE, INVISIBLE (in the file but not on the page), RENDER-ONLY, or MISSING.
    region is "page" or "x0,y0,x1,y1" as page fractions; with a region and auto=true every
    word the text layer places in that region must also be legible. A tool failure
    (missing poppler or tesseract, render error) returns REFUSE with the reason and makes
    no finding about the document. Proves legibility only, not correctness."""
    return check_legibility(pdf_path, require=values or [], page=page, region=region,
                            auto=auto, dpi=dpi)


@mcp.tool(
    title="SHA-256 of a file",
    annotations=ToolAnnotations(title="SHA-256 of a file", readOnlyHint=True,
                                destructiveHint=False, openWorldHint=False),
)
def pdf_sha256(path: str) -> dict[str, Any]:
    """Return the SHA-256 digest of a file so the exact output can be recorded."""
    return {"path": path, "sha256": sha256_file(path), "server_version": __version__}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
