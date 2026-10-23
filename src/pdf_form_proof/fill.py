"""Inspect and fill AcroForm PDF forms, refusing anything it cannot apply.

Rules this module keeps:
  - The source PDF is never modified. Output goes to a new path, and an
    existing output file is never overwritten.
  - Strict by default: if any requested field name does not exist in the
    form, nothing is written and the unknown names are returned. A partial
    fill is never hidden behind a success message.
  - Checkbox and radio fields get both /V and the widget /AS set, so the
    check mark renders instead of only existing in the data.
  - Every written file is returned with its SHA-256.

Limit: XFA-only forms (Adobe LiveCycle dynamic forms with no AcroForm
fields) are detected and refused; this module does not fill XFA.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from ._acroform import (
    _apply_checkbox_states,
    _btn_field_names,
    _widget_on_states,
    _qualified_name,
    detect_form_kind,
)

# pypdf logs a warning per page with no fields; keep stdio clean for MCP.
logging.getLogger("pypdf").setLevel(logging.ERROR)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _button_on_states(reader: PdfReader) -> dict[str, list[str]]:
    """{qualified field name: [on-state names]} for checkbox/radio widgets."""
    out: dict[str, list[str]] = {}
    for page in reader.pages:
        for ref in page.get("/Annots") or []:
            try:
                annot = ref.get_object()
            except Exception:
                continue
            if annot.get("/Subtype") != "/Widget":
                continue
            states = _widget_on_states(annot)
            if not states:
                continue
            name = (_qualified_name(annot) or "").rstrip(".")
            if not name:
                continue
            for s in states:
                out.setdefault(name, [])
                if s not in out[name]:
                    out[name].append(s)
    return out


def inspect_form(pdf_path: str) -> dict[str, Any]:
    """Return the form kind and every fillable field with its type and,
    for checkboxes and radios, the on-state values that will check them."""
    kind = detect_form_kind(pdf_path)
    if kind != "acroform":
        return {"pdf_path": pdf_path, "kind": kind, "field_count": 0, "fields": [],
                "note": ("XFA-only form: this tool does not fill XFA."
                         if kind == "xfa" else "No AcroForm fields found.")}
    reader = PdfReader(pdf_path)
    fields = reader.get_fields() or {}
    on_states = _button_on_states(reader)
    out = []
    for name, fobj in fields.items():
        ftype = fobj.get("/FT")
        if ftype is None:
            continue  # container node, not a fillable field
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
    return {"pdf_path": pdf_path, "kind": kind, "field_count": len(out), "fields": out}


def fill_form(source_pdf_path: str, output_path: str, values: dict[str, Any],
              strict: bool = True, finalize: bool = False) -> dict[str, Any]:
    """Fill fields by exact qualified name.

    values: {field name: value}. Text fields take a string. Checkbox/radio
    fields take an on-state ("/1", "/Yes") or True to check, and "", False,
    or "Off" to clear.

    finalize=True removes any /XFA packet and the /NeedAppearances flag so
    viewers render the values from their own appearance streams. It does NOT
    flatten the form: fields stay editable.
    """
    if not os.path.isfile(source_pdf_path):
        return {"status": "refused", "reason": f"no such file: {source_pdf_path}"}
    if os.path.abspath(source_pdf_path) == os.path.abspath(output_path):
        return {"status": "refused", "reason": "output_path equals source; the source is never modified"}
    if os.path.exists(output_path):
        return {"status": "refused", "reason": f"output exists, not overwritten: {output_path}"}
    kind = detect_form_kind(source_pdf_path)
    if kind != "acroform":
        return {"status": "refused", "reason": f"form kind is {kind!r}; only AcroForm forms are filled"}

    reader = PdfReader(source_pdf_path)
    known = set((reader.get_fields() or {}).keys())
    unknown = sorted(k for k in values if k not in known)
    if unknown and strict:
        return {"status": "refused", "reason": "field names not in this form; nothing written",
                "unknown_fields": unknown,
                "hint": "run pdf_form_inspect and copy names exactly"}

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

    finalize_changes = []
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
        except Exception as e:  # report, do not hide
            finalize_changes.append(f"finalize error: {e}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)

    # Read back: every text value must be present on the written file.
    back = PdfReader(output_path).get_fields() or {}
    mismatched = [k for k, v in text_values.items()
                  if str(back.get(k, {}).get("/V", "")) != v]
    status = "ok" if not (mismatched or cb["unresolved_on_state"] or cb["no_widget_match"]) else "incomplete"
    return {
        "status": status,
        "output_path": output_path,
        "sha256": sha256_file(output_path),
        "text_fields_set": len(text_values) - len(mismatched),
        "text_fields_not_read_back": mismatched,
        "checkboxes_set": cb["applied"],
        "checkbox_unresolved_on_state": cb["unresolved_on_state"],
        "checkbox_no_widget_match": cb["no_widget_match"],
        "unknown_fields_skipped": unknown,
        "finalize": finalize_changes if finalize else "not requested",
        "next_step": "run pdf_form_verify on the output to prove the values render",
    }
