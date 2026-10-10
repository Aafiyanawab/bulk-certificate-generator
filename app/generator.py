"""PDF certificate rendering using ReportLab.

There is exactly one predefined template (landscape A4, double border, centred text).
`generate_certificate` is intentionally a plain function with no database knowledge so it
is easy to test and easy to swap for another renderer.
"""
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1F3A5F")
GOLD = colors.HexColor("#B8860B")


@dataclass
class CertificateData:
    recipient_name: str
    achievement: str
    event_name: str
    issued_by: str
    issue_date: date
    certificate_number: str


def _fit_font_size(text: str, font: str, max_size: int, max_width: float, min_size: int = 14) -> int:
    """Shrink the font until the text fits within max_width."""
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _ensure_renderable(*texts: str) -> None:
    """The built-in PDF fonts only cover Latin-1/CP1252 characters. Fail loudly (and
    per-certificate) instead of silently printing black boxes for other scripts."""
    for t in texts:
        try:
            t.encode("cp1252")
        except UnicodeEncodeError as exc:
            bad = t[exc.start]
            raise ValueError(f"Unsupported character {bad!r} in {t!r}: the certificate font only supports Latin characters") from exc


def generate_certificate(data: CertificateData, out_path: Path) -> Path:
    """Render one certificate to `out_path` (written atomically) and return the path."""
    _ensure_renderable(data.recipient_name, data.achievement, data.event_name, data.issued_by)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".pdf.tmp")  # never leave a half-written file at the final path

    try:
        c = canvas.Canvas(str(tmp_path), pagesize=(PAGE_W, PAGE_H))
        c.setTitle(f"Certificate - {data.recipient_name}")

        # Borders
        c.setStrokeColor(NAVY)
        c.setLineWidth(6)
        c.rect(25, 25, PAGE_W - 50, PAGE_H - 50)
        c.setStrokeColor(GOLD)
        c.setLineWidth(2)
        c.rect(40, 40, PAGE_W - 80, PAGE_H - 80)

        cx = PAGE_W / 2
        max_text_w = PAGE_W - 160

        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 40)
        c.drawCentredString(cx, PAGE_H - 120, "CERTIFICATE")
        c.setFont("Helvetica", 18)
        c.drawCentredString(cx, PAGE_H - 150, "OF ACHIEVEMENT")

        c.setFillColor(colors.black)
        c.setFont("Helvetica", 16)
        c.drawCentredString(cx, PAGE_H - 215, "This is to certify that")

        name_size = _fit_font_size(data.recipient_name, "Helvetica-Bold", 38, max_text_w)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", name_size)
        c.drawCentredString(cx, PAGE_H - 270, data.recipient_name)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1)
        c.line(cx - 200, PAGE_H - 282, cx + 200, PAGE_H - 282)

        c.setFillColor(colors.black)
        ach_size = _fit_font_size(data.achievement, "Helvetica", 16, max_text_w, min_size=9)
        c.setFont("Helvetica", ach_size)
        c.drawCentredString(cx, PAGE_H - 320, data.achievement)

        ev_size = _fit_font_size(data.event_name, "Helvetica-Bold", 26, max_text_w, min_size=12)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", ev_size)
        c.drawCentredString(cx, PAGE_H - 360, data.event_name)

        # Footer
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 12)
        c.drawString(90, 90, f"Date: {data.issue_date.strftime('%d %B %Y')}")
        c.drawRightString(PAGE_W - 90, 90, f"Issued by: {data.issued_by}")
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.grey)
        c.drawCentredString(cx, 60, f"Certificate No. {data.certificate_number}")

        c.showPage()
        c.save()
        os.replace(tmp_path, out_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return out_path
