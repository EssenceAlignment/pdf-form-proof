# pdf-form-proof

Fill a fillable PDF form, then prove every value actually shows on the page.

mcp-name: io.github.EssenceAlignment/pdf-form-proof

A PDF can hold a value in its data while the page a person sees, prints, or
uploads shows nothing, or shows it cut off. Most form tools stop at "the field
was set." This one keeps going: it renders the page, reads it back with OCR
(optical character recognition, software that reads text from an image), and
compares that with the text stored in the file. Each value comes back VISIBLE,
INVISIBLE, RENDER-ONLY, or MISSING.

It works as an MCP server (MCP, the Model Context Protocol, is the plug-in
standard that lets AI apps such as Claude call outside tools), as a Claude
plugin, as a Gemini CLI extension, and as plain command-line tools.

## What it does

| Tool | What it does | Changes files? |
|---|---|---|
| `pdf_form_inspect` | Lists every fillable field: exact name, type, current value, required flag, and the values that check each checkbox or radio button | No |
| `pdf_form_fill` | Fills fields by exact name and writes a new PDF. Refuses the whole fill if any name is not in the form. Never modifies the source, never overwrites an existing file. Returns the new file's SHA-256 and reads every text value back | Writes one new file |
| `pdf_form_verify` | Renders a page, OCRs it, and gives each value a verdict. Can be limited to one region of the page so a copy of the value elsewhere cannot make it pass | No |
| `pdf_sha256` | SHA-256 fingerprint of a file, for a record of exactly what was produced | No |

Every tool carries MCP annotations: `readOnlyHint` and `destructiveHint` are set on all four.

## Who it is for

Anyone who fills the same official PDFs again and again: small nonprofits and
community organizations (vendor forms such as the IRS W-9, grant and funder
forms, program intake and consent forms), operations staff, and builders who
want an AI assistant to fill forms without hiding a partial or invisible fill.

## Install

You need Python 3.10 or newer and `uv` (https://docs.astral.sh/uv/). The verify
tool also needs two free programs:

- macOS: `brew install poppler tesseract`
- Debian or Ubuntu: `sudo apt install poppler-utils tesseract-ocr`

### Claude Desktop (MCP server)

Add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pdf-form-proof": {
      "command": "uvx",
      "args": ["pdf-form-proof"]
    }
  }
}
```

Or install the `.mcpb` file from the latest GitHub release: double-click it, or
use Settings > Extensions > Advanced settings > Install Extension.

### Claude Code (plugin)

```
claude plugin marketplace add EssenceAlignment/pdf-form-proof
claude plugin install pdf-form-proof@pdf-form-proof
```

### Gemini CLI (extension)

```
gemini extensions install https://github.com/EssenceAlignment/pdf-form-proof
```

### Command line

```
uvx --from pdf-form-proof pdf-form-inspect form.pdf
uvx --from pdf-form-proof pdf-form-fill form.pdf filled.pdf values.json
uvx --from pdf-form-proof pdf-form-verify filled.pdf --require "Sample Community Food Bank"
```

`values.json` maps exact field names (from `pdf-form-inspect`) to values:

```json
{
  "topmostSubform[0].Page1[0].f1_01[0]": "Sample Community Food Bank",
  "topmostSubform[0].Page1[0].Boxes3a-b_ReadOrder[0].c1_1[0]": "/1"
}
```

Text fields take a string. A checkbox or radio takes one of its on-state values
(shown by inspect, for example "/1" or "/Yes"), or an empty string to clear it.

Exit codes: 0 success; 1 a finding (verify found a value that is not visible,
or a fill could not be read back); 2 refused (unknown field names, a missing
file, an existing output, or a tool such as tesseract failed).

## Example

```
$ pdf-form-verify filled.pdf --require "Sample Community Food Bank" --require "Sample Pantry Program"
PDF FORM PROOF - legibility check  verdict: PASS
  file: filled.pdf  page 1 at 300 dpi  scope: whole page
  VALUE                       SOURCE    TEXT  RENDER  VERDICT
  Sample Community Food Bank  required  yes   yes     VISIBLE
  Sample Pantry Program       required  yes   yes     VISIBLE
  limit: legibility only; says nothing about whether values are correct
```

## Limits (read these)

- AcroForm forms only. XFA-only forms (Adobe LiveCycle dynamic forms) are
  detected and refused, not filled.
- `finalize` removes the XFA packet and the NeedAppearances flag so viewers draw
  the values from their own appearance streams. It does not flatten the form;
  fields stay editable.
- Verify proves legibility, not correctness. It cannot tell you whether a value
  is the right answer for the form.
- OCR can misread small, stylized, or handwritten-style fonts. A REFUSE verdict
  means "look at the page," not "the file is broken." A tool failure (missing
  poppler or tesseract, a render error) is reported as a tool failure, never as
  a finding about your document.
- Signatures are not created. Signature fields are listed by inspect and left alone.
- This tool fills forms. It does not give legal, tax, benefits, or other
  professional advice, and it does not decide what belongs in any field.

## Privacy policy

pdf-form-proof runs entirely on your own computer.

- Data collection: none. It reads only the PDF files you name and writes only
  the output path you name.
- Usage and storage: each rendered page image is written to a temporary folder
  under `~/.cache/pdf-form-proof` and deleted as soon as the OCR step finishes,
  including when a step fails. Nothing stays on disk unless you ask for a copy
  with `--keep-render`. (Version 0.1.0 kept the last image until the next run;
  0.1.1 removes any such leftover on its first run.)
- Third-party sharing: none. It makes no network calls and sends no telemetry.
- Retention: nothing is retained beyond the files you create.
- Contact: open an issue at https://github.com/EssenceAlignment/pdf-form-proof/issues

## Origin

The fill engine and the visible-on-the-page check were built for a production
document pipeline after a filled form passed every text-based check and was
still returned because a value could not be seen on the page. The two rules
that came out of that: never hide a partial fill, and check the page a person
sees, not only the data in the file.

## Author

Built by Eric Brakebill-Jones, MSW Systems Architect | Clinical Workflow and Grant Data Lineage.
GitHub: https://github.com/EssenceAlignment

## Changes

- 0.1.1: the rendered page image is deleted the moment OCR finishes (it was
  kept until the next run in 0.1.0). Author line added.
- 0.1.0: first release.

## License

MIT. See `LICENSE`.
