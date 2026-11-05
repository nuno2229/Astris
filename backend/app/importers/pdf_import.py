"""PDF statements (text layer) and scanned PDFs / photos via OCR — E03-04, E03-05."""
import io
import shutil
import warnings

from app.importers.base import ImportError_, ParseResult
from app.importers.text_statement import parse_statement_lines

MAX_PAGES = 40
MAX_PIXELS = 40_000_000  # per image; guards against decompression bombs


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def _ocr_image(img) -> str:
    import pytesseract
    if not ocr_available():
        raise ImportError_("This looks like a scan or photo, but OCR isn't installed on this server "
                           "(Tesseract). The Docker image includes it.")
    img = img.convert("L")
    return pytesseract.image_to_string(img, config="--psm 6")


def parse_pdf(raw: bytes) -> ParseResult:
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise ImportError_(f"PDF has more than {MAX_PAGES} pages; split it and import in parts")
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except ImportError_:
        raise
    except Exception as e:
        raise ImportError_(f"Couldn't read this PDF ({type(e).__name__})")
    if len(text.strip()) >= 40:
        return parse_statement_lines(text.splitlines(), "pdf")
    # no text layer -> scanned: render pages and OCR them
    import pypdfium2 as pdfium
    try:
        doc = pdfium.PdfDocument(raw)
    except Exception as e:
        raise ImportError_(f"Couldn't read this PDF ({type(e).__name__})")
    lines: list[str] = []
    try:
        for i in range(min(len(doc), MAX_PAGES)):
            page = doc[i]
            w, h = page.get_size()
            scale = min(300 / 72, (MAX_PIXELS / max(w * h, 1)) ** 0.5)
            lines += _ocr_image(page.render(scale=scale).to_pil()).splitlines()
    finally:
        doc.close()
    res = parse_statement_lines(lines, "pdf-ocr")
    res.warnings.insert(0, "Read from a scan with OCR. Check amounts before relying on them.")
    return res


def parse_image(raw: bytes) -> ParseResult:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(raw))
            img.load()
    except ImportError_:
        raise
    except Exception:
        raise ImportError_("Couldn't open this image")
    res = parse_statement_lines(_ocr_image(img).splitlines(), "image-ocr")
    res.warnings.insert(0, "Read from a photo with OCR. Check amounts before relying on them.")
    return res
