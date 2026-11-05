import io
from datetime import date
from decimal import Decimal

import pytest

from app.importers.pdf_import import ocr_available
from app.importers.text_statement import parse_statement_lines
from tests.conftest import create_account, make_user, upload

MT940 = b""":20:STMT2026110501
:25:12345678/GB00BARC20000012345678
:28C:00001/001
:60F:C261031GBP1000,00
:61:2611021102D45,10NMSCNONREF//B1
:86:ALDI STORES 4521
:61:2611041104C2100,00NTRFREF778//B2
:86:ACME LTD SALARY
:62F:C261105GBP3054,90
"""


def make_pdf(lines: list[str], text_layer: bool = True) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for ln in lines:
        c.drawString(40, y, ln)
        y -= 18
    c.save()
    return buf.getvalue()


UK_PDF_LINES = [
    "Barclays Bank UK PLC        Statement 1 Nov 2026 - 5 Nov 2026",
    "Sort code 20-00-00   Account number 12345678",
    "Date        Description                    Paid out     Paid in      Balance",
    "01 Nov      Balance brought forward                                  1,000.00",
    "01 Nov      TESCO STORES 2041              42.10                     957.90",
    "            COSTA COFFEE                   3.20                      954.70",
    "04 Nov      ACME LTD SALARY                             2,100.00     3,054.70",
    "05 Nov      REFUND AMAZON                               9.99         3,064.69",
    "Total payments 45.30   Total receipts 2,109.99",
]


def test_line_reader_uses_balance_for_direction():
    r = parse_statement_lines(UK_PDF_LINES, "pdf")
    got = [(t.posted_on, t.amount, t.description) for t in r.transactions]
    assert got == [
        (date(2026, 11, 1), Decimal("-42.10"), "TESCO STORES 2041"),
        (date(2026, 11, 1), Decimal("-3.20"), "COSTA COFFEE"),      # continuation row keeps the date
        (date(2026, 11, 4), Decimal("2100.00"), "ACME LTD SALARY"),
        (date(2026, 11, 5), Decimal("9.99"), "REFUND AMAZON"),     # money in detected from balance rising
    ]
    assert r.transactions[-1].running_balance == Decimal("3064.69")
    assert not r.warnings


def test_line_reader_explicit_markers_and_guess_warning():
    r = parse_statement_lines([
        "2026-11-02 CARD PAYMENT ALDI 18.45 DR",
        "2026-11-03 TRANSFER FROM SAVINGS 50.00 CR",
        "2026-11-03 SOMETHING 7.00",
        "03/11/2026 BRACKETED (12.00)",
    ], "pdf")
    assert [t.amount for t in r.transactions] == [Decimal("-18.45"), Decimal("50.00"), Decimal("-7.00"), Decimal("-12.00")]
    assert any("1 transaction" in w for w in r.warnings)


def test_line_reader_ignores_reference_numbers_and_dates_as_money():
    r = parse_statement_lines(["01/11/2026 PAYMENT REF 12345678 ON 01/11/2026 25.00 975.00",
                               "Opening balance 1,000.00"], "pdf")
    # opening balance line appears after, so the sign can't come from the balance; '25.00' is the amount
    assert r.transactions[0].amount.copy_abs() == Decimal("25.00")
    assert r.transactions[0].running_balance == Decimal("975.00")


def test_text_pdf_import_end_to_end():
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "statement.pdf", make_pdf(UK_PDF_LINES))
    assert r.status_code == 201, r.text
    assert r.json()["format"] == "pdf" and r.json()["rows_new"] == 4
    assert Decimal(a.get(f"/api/accounts/{acc}").json()["balance"]) == Decimal("3064.69")
    assert upload(a, acc, "statement.pdf", make_pdf(UK_PDF_LINES)).json()["rows_new"] == 0  # dedupe


def test_pdf_without_transactions_is_422():
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "x.pdf", make_pdf(["Hello, this is a letter about nothing in particular at all."]))
    assert r.status_code == 422 and "Couldn't find any transactions" in r.json()["detail"]


def test_mt940_import():
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "stmt.sta", MT940)
    assert r.status_code == 201, r.text
    assert r.json()["format"] == "mt940" and r.json()["rows_new"] == 2
    txs = a.get(f"/api/accounts/{acc}/transactions").json()
    assert [(t["description"], Decimal(t["amount"])) for t in txs] == [
        ("ACME LTD SALARY", Decimal("2100")), ("ALDI STORES 4521", Decimal("-45.10"))]
    assert Decimal(a.get(f"/api/accounts/{acc}").json()["balance"]) == Decimal("3054.90")


def _png() -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1400, 320), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
    except OSError:
        f = ImageFont.load_default(size=34)
    for i, ln in enumerate(["01/11/2026 Balance brought forward 1,000.00",
                            "02/11/2026 TESCO STORES 42.10 957.90",
                            "04/11/2026 ACME SALARY 2,100.00 3,057.90"]):
        d.text((30, 30 + i * 90), ln, fill="black", font=f)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.skipif(ocr_available(), reason="checks the message shown when OCR is missing")
def test_photo_without_ocr_explains():
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "photo.png", _png())
    assert r.status_code == 422 and "OCR isn't installed" in r.json()["detail"]


@pytest.mark.skipif(not ocr_available(), reason="Tesseract not installed (runs in CI and Docker)")
def test_photo_ocr_import():
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "photo.png", _png())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["format"] == "image-ocr" and body["rows_new"] == 2 and "OCR" in body["warnings"][0]
    amounts = sorted(Decimal(t["amount"]) for t in a.get(f"/api/accounts/{acc}/transactions").json())
    assert amounts == [Decimal("-42.10"), Decimal("2100.00")]


def test_image_bomb_rejected():
    a = make_user("a")
    acc = create_account(a)
    # PNG header claiming 100k x 100k pixels
    import struct
    import zlib
    ihdr = struct.pack(">IIBBBBB", 100000, 100000, 8, 2, 0, 0, 0)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    bomb = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
    r = upload(a, acc, "bomb.png", bomb)
    assert r.status_code == 422


def test_mt940_never_reads_server_files(tmp_path):
    """A 'statement' whose content is a file path must not make the server open that file."""
    secret = tmp_path / "secret.sta"
    secret.write_bytes(MT940)
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "x.sta", str(secret).encode())
    assert r.status_code == 422


def test_damaged_pdf_fails_cleanly():
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "x.pdf", b"%PDF-1.7\n" + b"\x00garbage" * 1000)
    assert r.status_code == 422


def test_worker_timeout_is_reported(monkeypatch):
    from app.importers import pdf_import
    monkeypatch.setattr(pdf_import, "WORKER_TIMEOUT_S", 0.001)
    a = make_user("a")
    acc = create_account(a)
    r = upload(a, acc, "statement.pdf", make_pdf(UK_PDF_LINES))
    assert r.status_code == 422 and "too long" in r.json()["detail"]
