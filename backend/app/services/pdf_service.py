import pymupdf

RENDER_DPI = 300
_PDF_HEADER = b"%PDF-"
_PDF_HEADER_SEARCH_WINDOW = 1024


class PDFProcessingError(ValueError):
    """Raised when input bytes cannot be processed as a PDF document."""


def has_pdf_header(file_bytes: bytes) -> bool:
    return _PDF_HEADER in file_bytes[:_PDF_HEADER_SEARCH_WINDOW]


def convert_pdf_to_images(file_bytes: bytes) -> list[bytes]:
    """Render every page of a PDF to PNG bytes at 300 DPI, in page order."""
    if not file_bytes:
        raise PDFProcessingError("File is empty")
    if not has_pdf_header(file_bytes):
        raise PDFProcessingError("File is not a PDF document")

    try:
        document = pymupdf.open(stream=file_bytes, filetype="pdf")
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError) as exc:
        raise PDFProcessingError("PDF file is corrupted or unreadable") from exc

    with document:
        if document.needs_pass:
            raise PDFProcessingError("PDF file is password-protected")
        if document.page_count == 0:
            raise PDFProcessingError("PDF file has no pages")

        matrix = pymupdf.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
        try:
            return [page.get_pixmap(matrix=matrix).tobytes("png") for page in document]
        except RuntimeError as exc:
            raise PDFProcessingError("Failed to render PDF page") from exc
