import pytest

from backend.app.constants import SENDING_ENTITIES
from backend.app.schemas import ConfidenceLevel
from backend.app.services.validator_service import (
    finalize_record,
    normalize_date,
    normalize_sender,
    validate_israeli_id,
)

VALID_ID = "123456782"


# --- validate_israeli_id ---------------------------------------------------


@pytest.mark.parametrize("raw", [VALID_ID, " 123456782 "])
def test_valid_id(raw):
    assert validate_israeli_id(raw) == (VALID_ID, True)


def test_short_id_is_zero_padded():
    assert validate_israeli_id("18") == ("000000018", True)


def test_invalid_check_digit():
    assert validate_israeli_id("123456789") == ("123456789", False)


@pytest.mark.parametrize("raw", ["12345678a", "123 456 782", "1234567890", "１２３４５６７８２"])
def test_malformed_id_is_returned_unchanged(raw):
    assert validate_israeli_id(raw) == (raw, False)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_id(raw):
    assert validate_israeli_id(raw) == (None, False)


def test_all_zero_id_is_invalid():
    assert validate_israeli_id("0") == ("000000000", False)


# --- normalize_date --------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "05/03/2024",
        "5/3/2024",
        "05.03.2024",
        "05-03-2024",
        "05/03/24",
        "05.03.24",
        "05-03-24",
        "2024-03-05",
        " 05/03/2024 ",
    ],
)
def test_supported_date_formats(raw):
    assert normalize_date(raw) == "2024-03-05"


@pytest.mark.parametrize("raw", [None, "", "31/02/2024", "13/13/2024", "March 5", "2024/03/05", "05/03/202"])
def test_invalid_or_missing_dates(raw):
    assert normalize_date(raw) is None


# --- normalize_sender ------------------------------------------------------


@pytest.mark.parametrize("canonical", list(SENDING_ENTITIES))
def test_every_canonical_name_matches_itself(canonical):
    assert normalize_sender(canonical) == (canonical, ConfidenceLevel.high)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("מכבי", "מכבי"),
        ("תל השומר", "שיבא (תל השומר)"),
        ("בית חולים שיבא", "שיבא (תל השומר)"),
        ("מכבי שירותי בריאות", "מכבי"),
        ("רמב״ם", 'רמב"ם'),
        ("רמב''ם", 'רמב"ם'),
        ("  הדסה   עין כרם ", "הדסה (עין כרם / הר הצופים)"),
        ("מדיתיק", 'מרפאות צה"ל (חר"פ / מדיתיק)'),
    ],
)
def test_sender_exact_and_phrase_matches(raw, expected):
    assert normalize_sender(raw) == (expected, ConfidenceLevel.high)


@pytest.mark.parametrize("raw, expected", [("סורוקא", "סורוקה"), ("בית חולים איכילב", "איכילוב (תל אביב)")])
def test_sender_fuzzy_match_is_medium_confidence(raw, expected):
    assert normalize_sender(raw) == (expected, ConfidenceLevel.medium)


@pytest.mark.parametrize("raw", ["בית חולים פרטי", "מכבי כללית"])
def test_unknown_or_ambiguous_sender_keeps_raw_value(raw):
    assert normalize_sender(raw) == (raw, ConfidenceLevel.low)


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_missing_sender(raw):
    assert normalize_sender(raw) == (None, ConfidenceLevel.low)


# --- finalize_record -------------------------------------------------------


def _box(ymin=0.1, xmin=0.1, ymax=0.2, xmax=0.3):
    return {"ymin": ymin, "xmin": xmin, "ymax": ymax, "xmax": xmax}


def _field(value, confidence="high", box=None):
    return {"value": value, "confidence": confidence, "box": box if box is not None else (_box() if value else None)}


def _payload(**overrides):
    payload = {
        "first_name": _field("דנה"),
        "last_name": _field("כהן"),
        "id_number": _field(VALID_ID),
        "received_date_stamp": _field("05/03/2024"),
        "received_date_handwritten": _field("04/03/2024"),
        "sender": _field("מכבי"),
        "central_numbers": [
            {"value": "123456", "confidence": "high", "source": "sticker", "box": _box()},
            {"value": "654321", "confidence": "medium", "source": "handwritten", "box": _box()},
        ],
        "commitment_numbers": [
            {"value": "A-1", "confidence": "high", "label_type": "commitment", "box": _box()},
        ],
        "requires_manual_review": False,
    }
    payload.update(overrides)
    return payload


def test_clean_payload_needs_no_review():
    record = finalize_record(_payload(), page_number=2)

    assert record.requires_manual_review is False
    assert record.review_reasons == []
    assert record.page_number == 2
    assert record.first_name.value == "דנה"
    assert record.first_name.box.page_number == 2
    assert record.id_number.value == VALID_ID
    assert record.received_date.value == "2024-03-05"
    assert record.received_date.raw_value == "05/03/2024"
    assert record.sender.value == "מכבי"
    assert [c.value for c in record.central_numbers] == ["123456", "654321"]
    assert [c.source for c in record.central_numbers] == ["sticker", "handwritten"]
    assert record.commitment_numbers[0].label_type == "commitment"
    assert record.status == "pending_review"


def test_invalid_id_flags_review_and_keeps_extracted_value():
    record = finalize_record(_payload(id_number=_field("123456789")), 1)

    assert record.id_number.value == "123456789"
    assert record.id_number.confidence is ConfidenceLevel.low
    assert "id_number_invalid" in record.review_reasons
    assert record.requires_manual_review is True


def test_short_id_is_padded_and_raw_value_preserved():
    record = finalize_record(_payload(id_number=_field("18")), 1)

    assert record.id_number.value == "000000018"
    assert record.id_number.raw_value == "18"
    assert record.requires_manual_review is False


def test_stamp_date_has_priority_over_handwritten():
    record = finalize_record(_payload(), 1)
    assert record.received_date.value == "2024-03-05"


def test_handwritten_date_used_when_no_stamp():
    record = finalize_record(_payload(received_date_stamp=_field(None, "low")), 1)

    assert record.received_date.value == "2024-03-04"
    assert record.requires_manual_review is False


def test_invalid_stamp_falls_back_to_handwritten_and_flags_review():
    record = finalize_record(_payload(received_date_stamp=_field("31/02/2024")), 1)

    assert record.received_date.value == "2024-03-04"
    assert "received_date_stamp_invalid" in record.review_reasons


def test_no_valid_date_keeps_raw_text_and_flags_review():
    record = finalize_record(
        _payload(received_date_stamp=_field("garbled"), received_date_handwritten=_field(None, "low")), 1
    )

    assert record.received_date.value is None
    assert record.received_date.confidence is ConfidenceLevel.low
    assert record.received_date.raw_value == "garbled"
    assert record.requires_manual_review is True


def test_missing_dates_flag_review():
    record = finalize_record(
        _payload(received_date_stamp=_field(None, "low"), received_date_handwritten=_field(None, "low")), 1
    )
    assert "received_date_missing" in record.review_reasons


def test_unknown_sender_preserves_raw_value_and_flags_review():
    record = finalize_record(_payload(sender=_field("מרפאה פרטית")), 1)

    assert record.sender.value == "מרפאה פרטית"
    assert record.sender.raw_value == "מרפאה פרטית"
    assert record.sender.confidence is ConfidenceLevel.low
    assert "sender_unrecognized" in record.review_reasons


def test_matched_sender_is_canonicalized_with_raw_value():
    record = finalize_record(_payload(sender=_field("בית חולים תל השומר")), 1)

    assert record.sender.value == "שיבא (תל השומר)"
    assert record.sender.raw_value == "בית חולים תל השומר"


def test_missing_fields_are_null_low_and_flagged():
    record = finalize_record(_payload(first_name=_field(None, "high"), last_name=None), 1)

    assert record.first_name.value is None
    assert record.first_name.confidence is ConfidenceLevel.low
    assert record.last_name.value is None
    assert {"first_name_missing", "last_name_missing"} <= set(record.review_reasons)


@pytest.mark.parametrize("bad_field", [{"value": 123456782, "confidence": "high", "box": None}, "דנה", ["x"]])
def test_invalid_field_types_become_null_and_flag_review(bad_field):
    record = finalize_record(_payload(first_name=bad_field), 1)

    assert record.first_name.value is None
    assert "first_name_invalid_type" in record.review_reasons


@pytest.mark.parametrize(
    "bad_box",
    [
        _box(ymin=-0.1),
        _box(xmax=1.5),
        _box(ymin=0.5, ymax=0.4),
        _box(xmin=0.5, xmax=0.4),
        {"ymin": 0.1},
        "0.1,0.2",
        {"ymin": "a", "xmin": 0, "ymax": 1, "xmax": 1},
    ],
)
def test_invalid_box_is_dropped_and_flags_review(bad_box):
    record = finalize_record(_payload(first_name=_field("דנה", box=bad_box)), 1)

    assert record.first_name.value == "דנה"
    assert record.first_name.box is None
    assert "first_name_invalid_box" in record.review_reasons


def test_invalid_item_box_flags_review():
    items = [{"value": "123456", "confidence": "high", "source": "sticker", "box": _box(ymin=2)}]
    record = finalize_record(_payload(central_numbers=items), 1)

    assert record.central_numbers[0].box is None
    assert "central_numbers[0]_invalid_box" in record.review_reasons


def test_unknown_confidence_defaults_to_low_and_flags_review():
    record = finalize_record(_payload(last_name=_field("כהן", confidence="certain")), 1)

    assert record.last_name.confidence is ConfidenceLevel.low
    assert "last_name_low_confidence" in record.review_reasons


def test_malformed_list_items_are_skipped_and_flagged():
    items = [
        {"value": "", "confidence": "high"},
        "123456",
        {"value": 7},
        {"value": "111111", "confidence": "high", "box": None},
    ]
    record = finalize_record(_payload(central_numbers=items, commitment_numbers="A-1"), 1)

    assert [c.value for c in record.central_numbers] == ["111111"]
    assert record.commitment_numbers == []
    assert {
        "central_numbers[0]_invalid",
        "central_numbers[1]_invalid",
        "central_numbers[2]_invalid",
        "commitment_numbers_invalid_type",
    } <= set(record.review_reasons)


def test_model_review_flag_is_respected():
    record = finalize_record(_payload(requires_manual_review=True), 1)
    assert record.review_reasons == ["flagged_by_model"]
