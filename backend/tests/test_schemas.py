import pytest
from pydantic import ValidationError

from backend.app.schemas import (
    BoundingBox,
    CentralNumberItem,
    CommitmentNumberItem,
    ConfidenceLevel,
    ExtractedRecord,
    FieldValue,
    RecordResponse,
)


def _empty_record(**overrides) -> ExtractedRecord:
    fields = dict(
        first_name=FieldValue(),
        last_name=FieldValue(),
        id_number=FieldValue(),
        received_date=FieldValue(),
        sender=FieldValue(),
    )
    fields.update(overrides)
    return ExtractedRecord(**fields)


def test_bounding_box_defaults_to_first_page():
    box = BoundingBox(ymin=0.1, xmin=0.2, ymax=0.3, xmax=0.4)
    assert box.page_number == 1


@pytest.mark.parametrize("edge", [0.0, 1.0])
def test_bounding_box_accepts_range_edges(edge):
    box = BoundingBox(ymin=edge, xmin=edge, ymax=edge, xmax=edge)
    assert box.ymin == edge


@pytest.mark.parametrize("field", ["ymin", "xmin", "ymax", "xmax"])
@pytest.mark.parametrize("bad_value", [-0.01, 1.01])
def test_bounding_box_rejects_out_of_range_coordinates(field, bad_value):
    coords = dict(ymin=0.0, xmin=0.0, ymax=1.0, xmax=1.0)
    coords[field] = bad_value
    with pytest.raises(ValidationError):
        BoundingBox(**coords)


@pytest.mark.parametrize(
    "coords",
    [
        dict(ymin=0.5, xmin=0.1, ymax=0.4, xmax=0.2),
        dict(ymin=0.1, xmin=0.5, ymax=0.2, xmax=0.4),
    ],
)
def test_bounding_box_rejects_inverted_coordinates(coords):
    with pytest.raises(ValidationError):
        BoundingBox(**coords)


def test_bounding_box_rejects_non_positive_page_number():
    with pytest.raises(ValidationError):
        BoundingBox(ymin=0.1, xmin=0.1, ymax=0.2, xmax=0.2, page_number=0)


def test_field_value_defaults_to_missing_low_confidence():
    field = FieldValue()
    assert field.value is None
    assert field.confidence is ConfidenceLevel.low
    assert field.box is None


def test_items_accept_optional_box():
    box = BoundingBox(ymin=0.1, xmin=0.2, ymax=0.3, xmax=0.4, page_number=2)
    central = CentralNumberItem(value="123", confidence="high", source="sticker", box=box)
    commitment = CommitmentNumberItem(value="456", confidence="medium")
    assert central.box == box
    assert commitment.box is None


def test_extracted_record_defaults():
    record = _empty_record()
    assert record.page_number == 1
    assert record.image_path is None
    assert record.central_numbers == []
    assert record.commitment_numbers == []
    assert record.requires_manual_review is False
    assert record.status == "pending_review"


def test_extracted_record_rejects_non_positive_page_number():
    with pytest.raises(ValidationError):
        _empty_record(page_number=0)


def test_extracted_record_list_defaults_are_not_shared():
    first = _empty_record()
    first.central_numbers.append(CentralNumberItem(value="1", confidence="low"))
    assert _empty_record().central_numbers == []


def test_extracted_record_round_trips_with_grounding():
    box = BoundingBox(ymin=0.1, xmin=0.2, ymax=0.3, xmax=0.4, page_number=3)
    record = _empty_record(
        page_number=3,
        image_path="pages/doc-3.png",
        first_name=FieldValue(value="Dana", confidence="high", box=box),
    )
    restored = ExtractedRecord.model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.first_name.box.page_number == 3


def test_record_response_requires_id_and_created_at():
    with pytest.raises(ValidationError):
        RecordResponse(**_empty_record().model_dump())
    response = RecordResponse(
        **_empty_record().model_dump(), id="rec-1", created_at="2026-01-01T00:00:00Z"
    )
    assert response.id == "rec-1"
