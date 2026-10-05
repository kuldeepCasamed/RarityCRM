"""Branded quote PDF (reportlab). Uses a bundled DejaVu font so the rupee sign renders;
falls back to Helvetica with 'Rs.' if the font can't be loaded."""
import io
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone

from ..pricing import format_money

ASSETS = Path(__file__).resolve().parent.parent / "assets"
BRAND = "#72383D"  # sampled from the logo
INK = "#2b2522"
MUTED = "#7a726c"
ZEBRA = "#f6f1ee"


def _fonts():
    """Returns (regular, bold, unicode_ok)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("DejaVu", str(ASSETS / "fonts" / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(ASSETS / "fonts" / "DejaVuSans-Bold.ttf")))
        return "DejaVu", "DejaVu-Bold", True
    except Exception:
        return "Helvetica", "Helvetica-Bold", False


def render_quote_pdf(quote) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold, unicode_ok = _fonts()
    sym = None if unicode_ok else {"INR": "Rs. "}.get(quote.currency)
    m = lambda v: format_money(v, quote.currency, sym)  # noqa: E731
    brand, ink, muted = colors.HexColor(BRAND), colors.HexColor(INK), colors.HexColor(MUTED)

    st = lambda name, **kw: ParagraphStyle(name, fontName=kw.pop("fontName", regular), textColor=kw.pop("textColor", ink), **kw)  # noqa: E731
    body = st("body", fontSize=9.5, leading=13)
    small = st("small", fontSize=8, leading=11, textColor=muted)
    h = st("h", fontName=bold, fontSize=10, leading=13, textColor=brand, spaceBefore=10, spaceAfter=3)
    right = st("right", fontSize=9.5, leading=13, alignment=TA_RIGHT)
    title = st("title", fontName=bold, fontSize=20, leading=24, textColor=brand, alignment=TA_RIGHT)
    P = lambda text, style=body: Paragraph(escape(str(text)).replace("\n", "<br/>"), style)  # noqa: E731

    lead = quote.lead
    items = list(quote.items.all())
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm,
                            title=f"Quote {quote.number}", author=settings.CLINIC_NAME)
    width = A4[0] - 36 * mm
    story = []

    # --- letterhead ---
    logo_path = ASSETS / "logo.png"
    logo = Image(str(logo_path), width=34 * mm, height=34 * mm * 244 / 537) if logo_path.exists() else P(settings.CLINIC_NAME, st("brand", fontName=bold, fontSize=16, textColor=brand))
    meta = [Paragraph("ESTIMATE", title),
            Paragraph(f"<b>{escape(quote.number)}</b>", right),
            Paragraph(f"Date: {timezone.localtime(quote.created_at):%d %b %Y}", right),
            Paragraph(f"Valid until: <b>{quote.valid_until:%d %b %Y}</b>", right)]
    head = Table([[logo, meta]], colWidths=[width * 0.5, width * 0.5])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 3 * mm)]

    contact = " · ".join(x for x in [settings.CLINIC_ADDRESS, settings.CLINIC_PHONE, settings.CLINIC_EMAIL, settings.CLINIC_WEBSITE] if x)
    if contact:
        story.append(P(contact, small))
    if settings.CLINIC_TAX_ID:
        story.append(P(f"GSTIN: {settings.CLINIC_TAX_ID}", small))
    rule = Table([[""]], colWidths=[width], rowHeights=[1])
    rule.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1.2, brand)]))
    story += [Spacer(1, 2 * mm), rule]

    # --- patient ---
    story.append(Paragraph("PREPARED FOR", h))
    story.append(P(lead.name, st("pn", fontName=bold, fontSize=11, leading=14)))
    for line in (lead.phone, lead.email, lead.country):
        if line:
            story.append(P(line, body))
    story.append(Spacer(1, 4 * mm))

    # --- items ---
    rows = [[P("#", st("th0", fontName=bold, fontSize=8.5, textColor=colors.white)),
             P("Treatment", st("th1", fontName=bold, fontSize=8.5, textColor=colors.white)),
             P("Qty", st("th2", fontName=bold, fontSize=8.5, textColor=colors.white, alignment=TA_RIGHT)),
             P("Unit price", st("th3", fontName=bold, fontSize=8.5, textColor=colors.white, alignment=TA_RIGHT)),
             P("Amount", st("th4", fontName=bold, fontSize=8.5, textColor=colors.white, alignment=TA_RIGHT))]]
    for n, it in enumerate(items, 1):
        desc = f"<b>{escape(it.description)}</b>" + (f"<br/><font size=8 color='{MUTED}'>{escape(it.area)}</font>" if it.area else "")
        rows.append([P(n, body), Paragraph(desc, body), P(it.quantity, right), P(m(it.unit_price), right), P(m(it.line_total), right)])
    table = Table(rows, colWidths=[9 * mm, width - 9 * mm - 14 * mm - 32 * mm - 34 * mm, 14 * mm, 32 * mm, 34 * mm], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), brand), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
             ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#e4dcd7"))]
    style += [("BACKGROUND", (0, r), (-1, r), colors.HexColor(ZEBRA)) for r in range(2, len(rows), 2)]
    table.setStyle(TableStyle(style))
    story += [table, Spacer(1, 3 * mm)]

    # --- totals ---
    t_rows = [[P("Subtotal", right), P(m(quote.subtotal), right)]]
    if quote.discount_amount:
        label = f"Discount ({Decimal(quote.discount_value).normalize():f}%)" if quote.discount_type == "percent" else "Discount"
        t_rows.append([P(label, right), P("-" + m(quote.discount_amount), right)])
    if quote.tax_amount or quote.tax_percent:
        t_rows.append([P(f"Tax ({Decimal(quote.tax_percent).normalize():f}%)", right), P(m(quote.tax_amount), right)])
    t_rows.append([Paragraph("TOTAL", st("tl", fontName=bold, fontSize=11, textColor=brand, alignment=TA_RIGHT)),
                   Paragraph(escape(m(quote.total)), st("tv", fontName=bold, fontSize=12, textColor=brand, alignment=TA_RIGHT))])
    totals = Table(t_rows, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 1, brand), ("TOPPADDING", (0, 0), (-1, -1), 2),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(totals)

    if quote.notes:
        story += [Paragraph("NOTES", h), P(quote.notes)]
    if quote.terms:
        story += [Paragraph("TERMS & CONDITIONS", h), P(quote.terms, small)]

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(18 * mm, 11 * mm, f"{settings.CLINIC_NAME} · {quote.number}")
        canvas.drawRightString(A4[0] - 18 * mm, 11 * mm, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
