import pymupdf
import pytest

from backend.app.services.pdf_service import PDFProcessingError, convert_pdf_to_images

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
A4_WIDTH_PT, A4_HEIGHT_PT = 595, 842


def _make_pdf(texts: list[str]) -> bytes:
    with pymupdf.open() as document:
        for text in texts:
            page = document.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
            page.insert_text((72, 72), text)
        return document.tobytes()


def test_two_page_pdf_yields_two_distinct_png_buffers():
    images = convert_pdf_to_images(_make_pdf(["Page one", "Page two"]))

    assert len(images) == 2
    assert all(image.startswith(PNG_SIGNATURE) for image in images)
    assert images[0] != images[1]


def test_pages_are_rendered_at_300_dpi():
    images = convert_pdf_to_images(_make_pdf(["First", "Second"]))

    expected_width = A4_WIDTH_PT * 300 / 72
    expected_height = A4_HEIGHT_PT * 300 / 72
    for image in images:
        pixmap = pymupdf.Pixmap(image)
        assert abs(pixmap.width - expected_width) <= 1
        assert abs(pixmap.height - expected_height) <= 1


def test_empty_bytes_are_rejected():
    with pytest.raises(PDFProcessingError, match="empty"):
        convert_pdf_to_images(b"")


def test_non_pdf_bytes_are_rejected():
    with pytest.raises(PDFProcessingError, match="not a PDF"):
        convert_pdf_to_images(PNG_SIGNATURE + b"not a pdf")


def test_corrupted_pdf_is_rejected():
    with pytest.raises(PDFProcessingError):
        convert_pdf_to_images(b"%PDF-1.7\n" + b"\x00garbage" * 50)


def test_password_protected_pdf_is_rejected():
    with pymupdf.open() as document:
        document.new_page()
        encrypted = document.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner"
        )

    with pytest.raises(PDFProcessingError, match="password"):
        convert_pdf_to_images(encrypted)
