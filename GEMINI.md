# pdf-form-proof

Tools: pdf_form_inspect, pdf_form_fill, pdf_form_verify, pdf_sha256.

Workflow for any fillable PDF form: inspect to get exact field names and
checkbox on-states; fill with exact names (unknown names are refused and
nothing is written); then verify the output so each value is confirmed
VISIBLE on the rendered page. Report REFUSE verdicts as-is. Never invent a
field value; ask the user. This tool fills forms and gives no legal, tax, or
benefits advice. The verify tool needs poppler and tesseract installed.
