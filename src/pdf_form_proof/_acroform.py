"""Low-level AcroForm helpers (pypdf). Field names, checkbox on-states,
form-kind detection, and the /V + /AS checkbox pass.

Extracted from a production form-filling server; logic unchanged.
"""

from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

# Values treated as "do not check" for checkbox fields. Everything else
# (True, "true", "Yes", "X", 1, "2", ...) means "set to the ON state".
_CHECKBOX_FALSY_STRINGS = {"", "0", "off", "false", "no", "none", "unchecked"}


def _checkbox_truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return val != 0
    return str(val).strip().lower() not in _CHECKBOX_FALSY_STRINGS


def _qualified_name(node) -> str | None:
    """Fully-qualified field name (parent /T chain joined with '.'),
    mirroring pypdf get_fields() key construction (/TM short-circuits)."""
    try:
        node = node.get_object()
    except Exception:
        pass
    try:
        if "/TM" in node:
            return str(node["/TM"])
        t = str(node["/T"]) if "/T" in node else None
        parent = node.get("/Parent")
        if parent is not None:
            pname = _qualified_name(parent)
            if t is None:
                return pname
            return f"{pname}.{t}" if pname else t
        return t
    except Exception:
        return None


def _widget_on_states(annot) -> list:
    """ON-state names (without leading '/') present in a widget's /AP /N
    (and /D) appearance dictionaries. '/Off' is excluded."""
    states: list = []
    try:
        ap = annot.get("/AP")
        if ap is None:
            return states
        ap = ap.get_object()
        for ap_key in ("/N", "/D"):
            sub = ap.get(ap_key)
            if sub is None:
                continue
            sub = sub.get_object()
            if not hasattr(sub, "keys"):
                continue  # appearance stream, not a state dictionary
            for k in sub.keys():
                name = str(k)
                if name == "/Off":
                    continue
                name = name[1:] if name.startswith("/") else name
                if name not in states:
                    states.append(name)
    except Exception:
        pass
    return states


def _btn_field_names(reader: PdfReader) -> set:
    """Fully-qualified names of /Btn (checkbox/radio) fields in the form."""
    names = set()
    try:
        fields = reader.get_fields() or {}
    except Exception:
        return names
    for name, fobj in fields.items():
        try:
            if str(fobj.get("/FT")) == "/Btn":
                names.add(name)
        except Exception:
            continue
    return names


def _apply_checkbox_states(writer: PdfWriter, checkbox_values: dict, on_state_map: dict) -> dict:
    """
    Set BOTH /V (on the field object) and /AS (on the widget annotation) for
    checkbox/radio fields. pypdf's update_page_form_field_values set /V only,
    so checkbox appearances did not render in some viewers.

    ON-state resolution order per field:
      (a) on_state_map -- an explicit {field: on_state} map from the caller
          (per-field values like "1", "2", "Yes", lowercase "yes");
      (b) else discovery from the widget /AP /N keys (first non-/Off key).
    """
    applied = []
    unresolved = []
    seen_keys = set()
    pushbutton_flag = 1 << 16  # /Ff bit 17: pushbutton (stateless; skip)

    for page in writer.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        for ref in annots:
            try:
                annot = ref.get_object()
            except Exception:
                continue
            if annot.get("/Subtype") != "/Widget":
                continue
            # Resolve the owning field node (where /FT and /V live).
            field_node = annot
            ftype = field_node.get("/FT")
            hops = 0
            while ftype is None and field_node.get("/Parent") is not None and hops < 16:
                field_node = field_node["/Parent"].get_object()
                ftype = field_node.get("/FT")
                hops += 1
            if str(ftype) != "/Btn":
                continue
            try:
                flags = int(field_node.get("/Ff", 0) or 0)
            except Exception:
                flags = 0
            if flags & pushbutton_flag:
                continue

            fq = (_qualified_name(annot) or "").rstrip(".")
            matched_key = None
            if fq and fq in checkbox_values:
                matched_key = fq
            elif fq:
                # Backslash-insensitive fallback: XFA-shadow leaf names carry
                # literal backslash-dot sequences (e.g. List17\.1[0]).
                fq_plain = fq.replace("\\", "")
                for k in checkbox_values:
                    if k.replace("\\", "") == fq_plain:
                        matched_key = k
                        break
            if matched_key is None:
                continue

            val = checkbox_values[matched_key]
            widget_states = _widget_on_states(annot)
            if _checkbox_truthy(val):
                target = str(on_state_map.get(matched_key) or "").lstrip("/")
                if not target:
                    target = widget_states[0] if widget_states else ""
                if not target:
                    if matched_key not in seen_keys:
                        unresolved.append(matched_key)
                        seen_keys.add(matched_key)
                    continue
                state = NameObject("/" + target)
                # /AS only where this widget owns the target state (radio
                # kids each own a single state); other kids go /Off.
                if not widget_states or target in widget_states:
                    annot[NameObject("/AS")] = state
                else:
                    annot[NameObject("/AS")] = NameObject("/Off")
                field_node[NameObject("/V")] = state
                applied.append({"field": matched_key, "state": "/" + target})
            else:
                annot[NameObject("/AS")] = NameObject("/Off")
                field_node[NameObject("/V")] = NameObject("/Off")
                applied.append({"field": matched_key, "state": "/Off"})
            seen_keys.add(matched_key)

    no_match = [k for k in checkbox_values if k not in seen_keys]
    return {"applied": applied, "unresolved_on_state": unresolved, "no_widget_match": no_match}


def detect_form_kind(pdf_path: str) -> str:
    """
    Return 'acroform', 'xfa', or 'none' based on PDF structure.
    XFA: has /AcroForm/XFA entry (Adobe LiveCycle dynamic forms).
    AcroForm: has /AcroForm/Fields with non-empty fields list.
    """
    try:
        reader = PdfReader(pdf_path)
    except Exception:
        return "none"
    root = reader.trailer.get("/Root", {})
    acroform = None
    try:
        acroform = root.get("/AcroForm")
    except Exception:
        acroform = None
    if not acroform:
        return "none"
    try:
        acro_obj = acroform.get_object() if hasattr(acroform, "get_object") else acroform
    except Exception:
        acro_obj = acroform
    has_xfa = False
    has_fields = False
    try:
        if "/XFA" in acro_obj:
            has_xfa = True
        if "/Fields" in acro_obj:
            fields = acro_obj.get("/Fields")
            if fields and len(fields) > 0:
                has_fields = True
    except Exception:
        pass
    if has_xfa and not has_fields:
        return "xfa"
    if has_fields and not has_xfa:
        return "acroform"
    if has_fields and has_xfa:
        # Hybrid XFA -- pypdf may fill the AcroForm shadow fields successfully
        return "acroform"
    return "none"


def list_acroform_fields(pdf_path: str) -> list[dict]:
    """
    Walk AcroForm fields and return [{name, type, value, flags, is_required}, ...].
    For XFA-only forms, returns empty list (caller should switch to xfa_handler).
    """
    reader = PdfReader(pdf_path)
    fields = reader.get_fields() or {}
    out = []
    for name, fobj in fields.items():
        ftype = fobj.get("/FT")
        out.append({
            "name": name,
            "type": str(ftype) if ftype else "unknown",
            "value": fobj.get("/V"),
            "flags": fobj.get("/Ff"),
            "is_required": bool(fobj.get("/Ff") and int(fobj.get("/Ff", 0)) & 2),
        })
    return out
