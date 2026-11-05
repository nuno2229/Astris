"""PDF statements (text layer) and scanned PDFs / photos via OCR — E03-04, E03-05.
Extraction runs in a sandboxed subprocess (extract_worker) with CPU/memory limits and a timeout."""
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from app.importers.base import ImportError_, ParseResult
from app.importers.text_statement import parse_statement_lines

WORKER_TIMEOUT_S = 150
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_slots = threading.BoundedSemaphore(2)  # at most 2 heavy extractions at once


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def _extract(kind: str, raw: bytes) -> dict:
    if not _slots.acquire(timeout=30):
        raise ImportError_("The server is busy reading other statements. Try again in a minute.")
    try:
        proc = subprocess.run([sys.executable, "-I", "-m", "app.importers.extract_worker", kind],
                              input=raw, capture_output=True, timeout=WORKER_TIMEOUT_S, cwd=_BACKEND_DIR)
    except subprocess.TimeoutExpired:
        raise ImportError_("Reading this file took too long. Try a smaller file or a clearer scan.")
    finally:
        _slots.release()
    try:
        out = json.loads(proc.stdout or b"{}")
    except ValueError:
        out = {}
    if "error" in out:
        raise ImportError_(out["error"])
    if proc.returncode != 0 or "lines" not in out:
        raise ImportError_("Couldn't read this file safely (it may be damaged or too complex)")
    return out


def parse_pdf(raw: bytes) -> ParseResult:
    out = _extract("pdf", raw)
    res = parse_statement_lines(out["lines"], "pdf-ocr" if out["ocr"] else "pdf")
    if out["ocr"]:
        res.warnings.insert(0, "Read from a scan with OCR. Check amounts before relying on them.")
    return res


def parse_image(raw: bytes) -> ParseResult:
    res = parse_statement_lines(_extract("image", raw)["lines"], "image-ocr")
    res.warnings.insert(0, "Read from a photo with OCR. Check amounts before relying on them.")
    return res
