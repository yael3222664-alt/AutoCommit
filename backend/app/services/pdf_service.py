import threading
from typing import Optional

import pymupdf

RENDER_DPI = 300
_PDF_HEADER = b"%PDF-"
_PDF_HEADER_SEARCH_WINDOW = 1024


class PDFProcessingError(ValueError):
    """Raised when input bytes cannot be processed as a PDF document."""


def has_pdf_header(file_bytes: bytes) -> bool:
    return _PDF_HEADER in file_bytes[:_PDF_HEADER_SEARCH_WINDOW]


class PdfPages:
    """An open PDF whose pages are rendered to PNG on demand, one page at a time.

    Rendering pages lazily keeps memory proportional to the pages in flight rather than
    to the document length. PyMuPDF documents are not thread-safe, so page access is
    serialized; PNG encoding of an already rendered pixmap runs outside the lock.
    """

    def __init__(self, document: pymupdf.Document) -> None:
        self._document: Optional[pymupdf.Document] = document
        self._page_count = document.page_count
        self._matrix = pymupdf.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
        self._lock = threading.Lock()

    @property
    def page_count(self) -> int:
        return self._page_count

    def render_png(self, page_number: int) -> bytes:
        """Render the 1-based `page_number` at 300 DPI."""
        if not 1 <= page_number <= self._page_count:
            raise PDFProcessingError(f"Page {page_number} is out of range")
        with self._lock:
            if self._document is None:
                raise PDFProcessingError("PDF document is closed")
            try:
                pixmap = self._document[page_number - 1].get_pixmap(matrix=self._matrix)
            except RuntimeError as exc:
                raise PDFProcessingError("Failed to render PDF page") from exc
            finally:
                # MuPDF caches decoded page images (up to 256 MB by default); a scanned
                # page's image is never reused, so drop it instead of letting it pile up.
                pymupdf.TOOLS.store_shrink(100)
        return pixmap.tobytes("png")

    def close(self) -> None:
        with self._lock:
            if self._document is not None:
                self._document.close()
                self._document = None

    def __enter__(self) -> "PdfPages":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def open_pdf(file_bytes: bytes) -> PdfPages:
    """Open and validate a PDF without rendering any page."""
    if not file_bytes:
        raise PDFProcessingError("File is empty")
    if not has_pdf_header(file_bytes):
        raise PDFProcessingError("File is not a PDF document")

    try:
        document = pymupdf.open(stream=file_bytes, filetype="pdf")
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError) as exc:
        raise PDFProcessingError("PDF file is corrupted or unreadable") from exc

    if document.needs_pass:
        document.close()
        raise PDFProcessingError("PDF file is password-protected")
    if document.page_count == 0:
        document.close()
        raise PDFProcessingError("PDF file has no pages")
    return PdfPages(document)


def convert_pdf_to_images(file_bytes: bytes) -> list[bytes]:
    """Render every page of a PDF to PNG bytes at 300 DPI, in page order."""
    with open_pdf(file_bytes) as pages:
        return [pages.render_png(number) for number in range(1, pages.page_count + 1)]
