from app.importers.base import ImportError_, ParseResult
from app.importers.csv_import import parse_csv
from app.importers.ofx_import import parse_ofx


def parse_statement(filename: str, raw: bytes) -> ParseResult:
    head = raw[:2048].lstrip().upper()
    name = filename.lower()
    if name.endswith((".ofx", ".qfx")) or head.startswith(b"OFXHEADER") or b"<OFX>" in head:
        return parse_ofx(raw)
    if name.endswith((".csv", ".txt", ".tsv")) or b"," in head or b";" in head:
        return parse_csv(raw)
    raise ImportError_("Unsupported file. M1 supports CSV and OFX/QFX; PDF, MT940 and images come later (E03).")
