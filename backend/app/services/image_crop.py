"""Cropping regions of an already rendered page image (no re-rendering, no enlargement)."""

from collections.abc import Mapping

import pymupdf

from ..schemas import BoundingBox

# Padding around a field box, as a fraction of the box size, with a floor relative to the page,
# so a tight or slightly misplaced box still contains the whole handwritten value.
BOX_MARGIN_RATIO = 0.25
MIN_MARGIN = 0.01


class ImageCropError(ValueError):
    """Raised when the page image cannot be decoded or a region is empty."""


def crop_regions(image_bytes: bytes, boxes: Mapping[str, BoundingBox]) -> dict[str, bytes]:
    """Return a PNG crop (at source resolution) of each normalized box, keyed like `boxes`.

    The page image is decoded once for all boxes and released before returning.
    """
    try:
        page = pymupdf.Pixmap(image_bytes)
    except Exception as exc:
        raise ImageCropError("Page image could not be decoded") from exc
    try:
        return {key: _crop(page, box) for key, box in boxes.items()}
    finally:
        del page


def padded_pixel_rect(box: BoundingBox, width: int, height: int) -> pymupdf.IRect:
    """Pixel rectangle of `box` plus margins, clipped to the image."""
    pad_y = max((box.ymax - box.ymin) * BOX_MARGIN_RATIO, MIN_MARGIN)
    pad_x = max((box.xmax - box.xmin) * BOX_MARGIN_RATIO, MIN_MARGIN)
    rect = pymupdf.IRect(
        int((box.xmin - pad_x) * width),
        int((box.ymin - pad_y) * height),
        round((box.xmax + pad_x) * width),
        round((box.ymax + pad_y) * height),
    )
    return rect & pymupdf.IRect(0, 0, width, height)


def _crop(page: pymupdf.Pixmap, box: BoundingBox) -> bytes:
    rect = padded_pixel_rect(box, page.width, page.height)
    if rect.is_empty:
        raise ImageCropError("Field region is empty")
    region = pymupdf.Pixmap(page.colorspace, rect, page.alpha)
    region.copy(page, rect)
    return region.tobytes("png")
