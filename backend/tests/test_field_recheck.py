"""Deterministic tests for field-level prompts, ambiguity, and the one-shot targeted re-read."""

import asyncio

import pymupdf
import pytest

from backend.app.services.extractor_service import (
    DATE_RECHECK_PROMPT,
    ID_RECHECK_PROMPT,
    RECHECK_SCHEMA,
    extract_record_from_image,
)
from backend.app.services.validator_service import (
    FieldReading,
    finalize_record,
    normalize_date,
    recheck_boxes,
    validate_israeli_id,
)
from backend.tests.fakes import FakeVisionProvider, provider_json, valid_payload


@pytest.fixture
def page_png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 600))
    pix.clear_with(255)
    return pix.tobytes("png")


def _extract(provider, image, page_number=1, recheck_fields=True):
    return asyncio.run(
        extract_record_from_image(image, page_number, provider=provider, recheck_fields=recheck_fields)
    )


def _page_with(**fields) -> str:
    return provider_json({**valid_payload(), **fields})


_DEFAULT_BOX = {"ymin": 0.1, "xmin": 0.1, "ymax": 0.2, "xmax": 0.3}


def _field(value, confidence="high", box=_DEFAULT_BOX, alternatives=None):
    field = {
        "value": value,
        "confidence": confidence,
        "box": box,
    }
    if alternatives is not None:
        field["alternatives"] = alternatives
    return field


def _recheck(value, confidence="high", alternatives=None):
    import json

    return json.dumps(
        {"value": value, "confidence": confidence, "alternatives": alternatives or []}
    )


# --- validation basics -------------------------------------------------------


def test_israeli_id_preserves_leading_zeros_and_checksum():
    assert validate_israeli_id("000000018") == ("000000018", True)
    assert validate_israeli_id("000000026") == ("000000026", True)
    assert validate_israeli_id("012345678") == ("012345678", False)
    assert validate_israeli_id("123456789") == ("123456789", False)
    assert validate_israeli_id("12345678") == ("012345678", False)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("29/02/2024", "2024-02-29"),   # leap year
        ("29/02/2023", None),           # impossible day
        ("31/04/2024", None),           # April has 30 days
        ("05/13/2024", None),           # impossible month
        ("5.3.24", "2024-03-05"),
        ("2024-03-05", "2024-03-05"),
        ("0?/03/2024", None),           # unreadable digit is never guessed
    ],
)
def test_date_calendar_validity(text, expected):
    assert normalize_date(text) == expected


# --- recheck target selection -------------------------------------------------


def test_recheck_boxes_only_picks_doubtful_fields():
    raw = {
        "id_number": _field("123456782"),  # valid + high confidence -> no doubt
        "received_date_stamp": _field("05/03/2024"),
        "received_date_handwritten": _field(None, "low", box=None),
    }
    assert recheck_boxes(raw, 4) == {}


def test_recheck_boxes_picks_invalid_low_confidence_and_missing_with_box():
    raw = {
        "id_number": _field("123456789"),                        # invalid checksum
        "received_date_stamp": _field("32/03/2024"),             # impossible date
        "received_date_handwritten": _field(None, "low"),        # missing but located
    }
    boxes = recheck_boxes(raw, 7)
    assert set(boxes) == {"id_number", "received_date_stamp", "received_date_handwritten"}
    assert all(box.page_number == 7 for box in boxes.values())


def test_recheck_boxes_skips_fields_without_a_box():
    raw = {"id_number": {"value": "123456789", "confidence": "low", "box": None}}
    assert recheck_boxes(raw, 1) == {}


# --- reconciliation -----------------------------------------------------------


def test_ambiguous_alternatives_require_manual_review():
    raw = valid_payload() | {"id_number": _field("123456782", alternatives=["123456789"])}
    record = finalize_record(raw, 1)
    assert record.id_number.value is None
    assert record.id_number.candidates == ["123456782", "123456789"]
    assert record.id_number.raw_value == "123456782"
    assert "id_number_ambiguous" in record.review_reasons
    assert record.requires_manual_review


def test_unreadable_digit_marker_requires_review():
    raw = valid_payload() | {"id_number": _field("12345?782")}
    record = finalize_record(raw, 1)
    assert "id_number_partially_unreadable" in record.review_reasons


def test_recheck_agreement_recovers_a_valid_reading():
    raw = valid_payload() | {"id_number": _field("000000018", confidence="low")}
    record = finalize_record(raw, 1, {"id_number": FieldReading(value="000000018", confidence="medium")})
    assert record.id_number.value == "000000018"
    assert record.id_number.confidence.value == "medium"
    assert record.id_number.raw_value == "000000018"
    assert record.review_reasons == []


def test_recheck_disagreement_keeps_all_readings():
    raw = valid_payload() | {"id_number": _field("123456789")}
    record = finalize_record(raw, 2, {"id_number": FieldReading(value="123456782", confidence="high")})
    assert record.id_number.value is None
    assert set(record.id_number.candidates) == {"123456789", "123456782"}
    assert "id_number_recheck_disagrees" in record.review_reasons


def test_recheck_never_repairs_a_bad_reading_on_its_own():
    # The crop read something valid that contradicts the page read: still a disagreement.
    raw = valid_payload() | {
        "received_date_stamp": _field("31/02/2024"),
        "received_date_handwritten": _field("04/03/2024"),
    }
    record = finalize_record(
        raw, 1, {"received_date_stamp": FieldReading(value="28/02/2024", confidence="high")}
    )
    assert record.received_date.value == "2024-03-04"  # falls back to the handwritten date
    assert "received_date_stamp_recheck_disagrees" in record.review_reasons


def test_failed_recheck_is_reported_not_retried():
    raw = valid_payload() | {"id_number": _field("123456789")}
    record = finalize_record(raw, 1, {"id_number": None})
    assert "id_number_recheck_failed" in record.review_reasons


# --- end to end through the extractor ----------------------------------------


def test_clean_page_makes_only_one_provider_call(page_png):
    provider = FakeVisionProvider()
    _extract(provider, page_png)
    assert len(provider.calls) == 1


def test_doubtful_id_gets_one_targeted_crop_call(page_png):
    provider = FakeVisionProvider(
        responses=[_page_with(id_number=_field("123456789")), _recheck("123456782")]
    )
    record = _extract(provider, page_png, page_number=5)

    assert len(provider.calls) == 2
    second = provider.calls[1]
    assert second["system_prompt"] == ID_RECHECK_PROMPT
    assert second["response_schema"] == RECHECK_SCHEMA
    assert second["mime_type"] == "image/png"
    crop = pymupdf.Pixmap(second["image_bytes"])
    assert crop.width < 400 and crop.height < 600

    assert record.id_number.value is None
    assert set(record.id_number.candidates) == {"123456789", "123456782"}
    assert "id_number_recheck_disagrees" in record.review_reasons
    # The kept box is the original full-page box on the original page.
    assert record.id_number.box == record.id_number.box
    assert record.id_number.box.page_number == 5


def test_recheck_is_skipped_when_disabled(page_png):
    provider = FakeVisionProvider(responses=[_page_with(id_number=_field("123456789"))])
    record = _extract(provider, page_png, recheck_fields=False)
    assert len(provider.calls) == 1
    assert "id_number_invalid" in record.review_reasons


def test_each_doubtful_field_gets_at_most_one_recheck(page_png):
    provider = FakeVisionProvider(
        responses=[
            _page_with(
                id_number=_field("123456789"),
                received_date_stamp=_field("32/03/2024"),
            ),
            _recheck("123456782"),          # id_number recheck
            _recheck("05/03/2024"),         # received_date_stamp recheck
        ]
    )
    _extract(provider, page_png)
    assert len(provider.calls) == 3  # exactly one re-read per field, never repeated
    prompts = {call["system_prompt"] for call in provider.calls[1:]}
    assert prompts == {ID_RECHECK_PROMPT, DATE_RECHECK_PROMPT}


def test_recheck_provider_failure_marks_review_once(page_png):
    provider = FakeVisionProvider(responses=[_page_with(id_number=_field("123456789"))])
    # second call has no scripted response -> the fake raises, recorded as a failed re-read
    record = _extract(provider, page_png)
    assert len(provider.calls) == 2
    assert "id_number_recheck_failed" in record.review_reasons
    assert "id_number_invalid" in record.review_reasons


def test_missing_field_with_location_is_rechecked(page_png):
    provider = FakeVisionProvider(
        responses=[
            _page_with(id_number=_field(None, "low")),
            _recheck("000000018"),
        ]
    )
    record = _extract(provider, page_png)
    assert len(provider.calls) == 2
    # The page read and the crop read disagree: no value is silently accepted.
    assert record.id_number.value is None
    assert record.id_number.candidates == ["000000018"]
    assert "id_number_recheck_disagrees" in record.review_reasons
