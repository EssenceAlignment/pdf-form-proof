"""Build the two public sample PDFs (no personal fields). Run: python samples/make_samples.py"""
import os
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))

def fillable(path):
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("Equipment Check (sample)")
    c.setFont("Helvetica-Bold", 16); c.drawString(72, 720, "Equipment Check - Sample Form")
    c.setFont("Helvetica", 10)
    c.drawString(72, 700, "A sample fillable form for testing PDF Form Proof. No personal fields.")
    f = c.acroForm
    rows = [("Organization name", "organization_name"), ("Contact name", "contact_name"),
            ("Date (YYYY-MM-DD)", "date"), ("Item count", "item_count")]
    y = 650
    c.setFont("Helvetica", 11)
    for label, name in rows:
        c.drawString(72, y + 4, label)
        f.textfield(name=name, x=240, y=y - 4, width=280, height=22, borderWidth=1,
                    fontSize=11, fillColor=None, textColor=None, fieldFlags="", tooltip=label)
        y -= 40
    c.drawString(72, y + 4, "Certified complete")
    f.checkbox(name="certified_complete", x=240, y=y - 2, size=18, checked=False,
               buttonStyle="check", borderWidth=1, tooltip="Certified complete")
    c.showPage(); c.save()

def flat(path):
    c = canvas.Canvas(path, pagesize=LETTER)
    c.setTitle("Flat page (sample)")
    c.setFont("Helvetica-Bold", 16); c.drawString(72, 720, "Flat Page - Sample")
    c.setFont("Helvetica", 11)
    c.drawString(72, 690, "Organization name: ______________________")
    c.drawString(72, 660, "Contact name: ___________________________")
    c.drawString(72, 630, "This page has no fillable fields. It is a flat PDF.")
    c.showPage(); c.save()

if __name__ == "__main__":
    fillable(os.path.join(HERE, "equipment-check.pdf"))
    flat(os.path.join(HERE, "flat-page.pdf"))
    print("wrote equipment-check.pdf and flat-page.pdf")
