from app.importers.base import ImportError_, ParseResult
from app.importers.csv_import import parse_csv
from app.importers.mt940_import import parse_mt940
from app.importers.ofx_import import parse_ofx
from app.importers.pdf_import import parse_image, parse_pdf

_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"II*\x00", b"MM\x00*")


def parse_statement(filename: str, raw: bytes, options: dict | None = None) -> ParseResult:
    """options (CSV only): {"mapping": {role: header}, "day_first": bool, "negate": bool}"""
    options = options or {}
    head = raw[:2048].lstrip()
    up = head.upper()
    name = filename.lower()
    if head.startswith(b"%PDF"):
        return parse_pdf(raw)
    if head.startswith(_IMAGE_MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return parse_image(raw)
    if name.endswith((".ofx", ".qfx")) or up.startswith(b"OFXHEADER") or b"<OFX>" in up:
        return parse_ofx(raw)
    if name.endswith((".sta", ".mt940", ".940")) or head.startswith((b":20:", b"{1:")) or b"\n:61:" in head:
        return parse_mt940(raw)
    if name.endswith((".csv", ".txt", ".tsv")) or b"," in head or b";" in head:
        return parse_csv(raw, options.get("mapping"), options.get("day_first"), options.get("negate"))
    raise ImportError_("Unsupported file. Use CSV, OFX/QFX, MT940, PDF, or a photo of a statement.")
