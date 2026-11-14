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


@pytest.mark.skipif(__import__("shutil").which("tesseract") is None, reason="needs the OCR engine (runs in CI)")
def test_receipt_photo_read_under_20_seconds():
    """E21-03: a phone photo of a 20-line receipt goes through OCR and item reading in under 20 s."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    from tests.conftest import create_household, make_user
    lines = ["TESCO", "Superstore Holloway"]
    items = [("SEMI SKIMMED MILK 2L", "1.65"), ("HEINZ BEANS 415G", "0.85"), ("WARBURTONS BREAD 800G", "1.40"), ("BANANAS LOOSE", "0.80"),
             ("CHEDDAR 400G", "3.25"), ("PASTA 500G", "0.75"), ("CHOPPED TOMATOES 400G", "0.55"), ("FREE RANGE EGGS 6PK", "1.95"),
             ("ANDREX TOILET ROLL 9PK", "5.00"), ("COLGATE TOOTHPASTE", "2.00"), ("APPLES 6PK", "1.60"), ("ORANGE JUICE 1L", "1.20"),
             ("BUTTER 250G", "1.99"), ("RICE 1KG", "1.10"), ("CHICKEN BREAST 600G", "4.50"), ("YOGHURT 500G", "1.15")]
    lines += [f"{n:<26}{p:>7}" for n, p in items]
    total = sum(float(p) for _, p in items)
    lines += [f"{'TOTAL':<26}{total:>7.2f}", f"{'VISA CONTACTLESS':<26}{total:>7.2f}", "12/11/2026 14:32"]
    font = None
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/System/Library/Fonts/Menlo.ttc"):
        try:
            font = ImageFont.truetype(f, 34)
            break
        except OSError:
            pass
    font = font or ImageFont.load_default(size=34)
    img = Image.new("RGB", (900, 120 + 48 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        d.text((50, 60 + 48 * i), ln, fill="black", font=font)
    photo = img.rotate(1.2, expand=True, fillcolor="#e8e4da").resize((1800, int(1800 * img.height / img.width))).filter(ImageFilter.GaussianBlur(0.8))
    buf = io.BytesIO()
    photo.save(buf, "JPEG", quality=85)
    a = make_user("a")
    hid = create_household(a)
    t0 = time.monotonic()
    r = a.post(f"/api/households/{hid}/receipts", files=[("files", ("receipt.jpg", buf.getvalue()))], data={"keep_original": "false"})
    took = time.monotonic() - t0
    assert r.status_code == 201, r.text
    rec = r.json()[0]
    assert len(rec["items"]) >= 12, [i["name"] for i in rec["items"]]      # most lines read
    assert took < 20, f"{took:.1f} s"
