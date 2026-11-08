"""E21-01 / G-2 (PO 2026-11-08): a 5,000-line text PDF statement is read in under 30 seconds."""
import io
import time
from datetime import date, timedelta

import pytest

reportlab = pytest.importorskip("reportlab")


def test_5000_line_pdf_under_30_seconds():
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from app.routers.ledger import parse_checked
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y, bal, d = 800, 100000.00, date(2025, 1, 1)
    c.drawString(40, y, "01 Jan 2025 Opening balance £100,000.00")
    y -= 14
    for i in range(5000):
        amt = round(3 + (i * 7) % 97 + 0.37, 2)
        bal = round(bal - amt, 2)
        c.drawString(40, y, f"{(d + timedelta(days=i // 14)).strftime('%d %b %Y')} SHOP NUMBER {i} LONDON -£{amt:,.2f} £{bal:,.2f}")
        y -= 14
        if y < 40:
            c.showPage()
            y = 800
    c.save()
    t0 = time.monotonic()
    r = parse_checked("big.pdf", buf.getvalue())
    assert len(r.transactions) == 5000 and r.warnings == []
    assert time.monotonic() - t0 < 30
