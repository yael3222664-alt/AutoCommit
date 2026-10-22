import pymupdf
import pytest

from backend.app.schemas import BoundingBox
from backend.app.services.image_crop import (
    ImageCropError,
    crop_regions,
    padded_pixel_rect,
)


@pytest.fixture
def page_png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 600))
    pix.clear_with(255)
    return pix.tobytes("png")


def _decode(png: bytes) -> pymupdf.Pixmap:
    return pymupdf.Pixmap(png)


def test_crop_returns_the_box_region_with_margins(page_png):
    box = BoundingBox(ymin=0.4, xmin=0.25, ymax=0.5, xmax=0.5, page_number=3)
    crops = crop_regions(page_png, {"id_number": box})
    crop = _decode(crops["id_number"])
    # box is 100x60 px; margins add 25% of the box on each side -> ~150x90 px
    assert 140 <= crop.width <= 160
    assert 80 <= crop.height <= 100
    assert len(crops["id_number"]) < len(page_png)


def test_crop_clips_at_page_edges(page_png):
    box = BoundingBox(ymin=0.0, xmin=0.0, ymax=0.05, xmax=0.05, page_number=1)
    crop = _decode(crop_regions(page_png, {"d": box})["d"])
    assert 0 < crop.width <= 400 and 0 < crop.height <= 600


def test_pixel_rect_never_leaves_the_image():
    rect = padded_pixel_rect(BoundingBox(ymin=0.0, xmin=0.0, ymax=1.0, xmax=1.0), 400, 600)
    assert rect == pymupdf.IRect(0, 0, 400, 600)


def test_undecodable_image_raises_crop_error():
    with pytest.raises(ImageCropError):
        crop_regions(b"not an image", {"d": BoundingBox(ymin=0, xmin=0, ymax=0.1, xmax=0.1)})


def test_multiple_boxes_share_one_decode(page_png):
    boxes = {
        "a": BoundingBox(ymin=0.1, xmin=0.1, ymax=0.2, xmax=0.2, page_number=2),
        "b": BoundingBox(ymin=0.6, xmin=0.6, ymax=0.7, xmax=0.8, page_number=2),
    }
    crops = crop_regions(page_png, boxes)
    assert set(crops) == {"a", "b"}
    assert _decode(crops["a"]).width != _decode(crops["b"]).width
