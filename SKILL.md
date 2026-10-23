---
name: pdf-form-proof
description: Fill a fillable (AcroForm) PDF form and prove the result is visible on the page. Use when asked to fill out, complete, or check a PDF form (W-9, grant, intake, consent, or any agency form), when a filled field "looks blank", or when a form must be verified before it is sent. Inspect exact field names, fill by name with unknown names refused, then OCR the rendered page to confirm each value shows.
---

# pdf-form-proof

Three steps, in order. Do not skip step 3.

## 1. Inspect

Call `pdf_form_inspect` (or run `pdf-form-inspect form.pdf`). Copy field names
exactly as returned. For checkboxes and radio buttons, note the `on_states`
list: that value (for example "/1") is what checks the box.

If `kind` is `xfa`, stop and tell the user this tool does not fill XFA forms.

## 2. Fill

Call `pdf_form_fill` with a new `output_path` and a `values` map of exact names.

- Leave `strict` on. If the result is `refused` with `unknown_fields`, fix the
  names from step 1. Never retry with `strict=false` to make an error go away.
- The source file is never modified and an existing output is never overwritten.
- Report the returned `sha256` to the user.
- Ask the user for any value you do not have. Never invent a value for a form
  field, and do not decide what an official form should say on the user's behalf.

## 3. Verify

Call `pdf_form_verify` on the output with the values you filled (one call per page).

- PASS: every value is VISIBLE. Say so, and repeat the limit: this proves the
  values show, not that they are correct.
- REFUSE with `kind: finding`: list each value marked INVISIBLE, RENDER-ONLY or
  MISSING. INVISIBLE means it is in the file but not on the page; the usual
  fix is re-filling with `finalize=true`, or a shorter value for a small box.
- REFUSE with `kind: tool_failure`: say which program failed (poppler or
  tesseract) and how to install it. Make no claim about the document.

## Limits

AcroForm only. No signatures. OCR can misread small or stylized fonts. This
skill fills forms; it does not give legal, tax, or benefits advice.
