"""Sandboxed text extraction for untrusted PDFs and images. Runs in a SEPARATE PROCESS with CPU and
memory limits, so a decompression bomb or pathological file can only kill this worker.

Usage: python -m app.importers.extract_worker {pdf|image} < file   ->  JSON {"lines": [...], "ocr": bool} | {"error": "..."}
"""
import io
import json
import sys
import warnings

MAX_PAGES = 40           # text-layer pages
MAX_OCR_PAGES = 10       # scanned pages (OCR is expensive)
MAX_CHARS_PER_PAGE = 20_000
MAX_PIXELS = 40_000_000  # per rendered page / image
OCR_TIMEOUT_S = 25       # per page


def _limit_resources() -> None:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (120, 125))
        if sys.platform.startswith("linux"):  # RLIMIT_AS isn't enforced on macOS
            resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 ** 2, 1536 * 1024 ** 2))
    except Exception:
        pass


class Fail(Exception):
    pass


def _ocr(img) -> str:
    import shutil
    import pytesseract
    if not shutil.which("tesseract"):
        raise Fail("This looks like a scan or photo, but OCR isn't installed on this server (Tesseract). "
                   "The Docker image includes it.")
    try:
        return pytesseract.image_to_string(img.convert("L"), config="--psm 6", timeout=OCR_TIMEOUT_S)
    except RuntimeError:  # pytesseract raises RuntimeError on timeout
        raise Fail("Reading this scan took too long. Try a clearer or smaller image.")


def extract_pdf(raw: bytes) -> dict:
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise Fail(f"PDF has more than {MAX_PAGES} pages; split it and import in parts")
            parts = []
            for p in pdf.pages:
                if len(p.chars) > MAX_CHARS_PER_PAGE:
                    raise Fail("This PDF has an unusually large amount of text on a page")
                parts.append(p.extract_text() or "")
            text = "\n".join(parts)
    except Fail:
        raise
    except Exception as e:
        raise Fail(f"Couldn't read this PDF ({type(e).__name__})")
    if len(text.strip()) >= 40:
        return {"lines": text.splitlines(), "ocr": False}
    import pypdfium2 as pdfium
    try:
        doc = pdfium.PdfDocument(raw)
    except Exception as e:
        raise Fail(f"Couldn't read this PDF ({type(e).__name__})")
    if len(doc) > MAX_OCR_PAGES:
        raise Fail(f"Scanned PDFs can have at most {MAX_OCR_PAGES} pages")
    lines: list[str] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            w, h = page.get_size()
            scale = min(300 / 72, (MAX_PIXELS / max(w * h, 1)) ** 0.5)
            lines += _ocr(page.render(scale=scale).to_pil()).splitlines()
    finally:
        doc.close()
    return {"lines": lines, "ocr": True}


def extract_image(raw: bytes) -> dict:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(raw))
            img.load()
    except Exception:
        raise Fail("Couldn't open this image")
    return {"lines": _ocr(img).splitlines(), "ocr": True}


def main() -> None:
    _limit_resources()
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = sys.stdin.buffer.read()
    try:
        out = extract_pdf(raw) if kind == "pdf" else extract_image(raw) if kind == "image" else {"error": "bad kind"}
    except Fail as e:
        out = {"error": str(e)}
    except MemoryError:
        out = {"error": "This file needs too much memory to read"}
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
