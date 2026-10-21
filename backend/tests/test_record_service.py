from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import RecordEditRow, RecordRow
from backend.app.db.session import create_db_engine, init_db
from backend.app.schemas import (
    BoundingBox,
    CentralNumberItem,
    CommitmentNumberItem,
    ExtractedRecord,
    FieldValue,
    RecordStatus,
    RecordUpdateSchema,
)
from backend.app.services.document_pipeline import build_failed_record
from backend.app.services.record_service import (
    InvalidRecordUpdateError,
    InvalidTransitionError,
    RecordNotFoundError,
    RecordService,
)

VALID_ID = "000000018"
BOX = BoundingBox(ymin=0.1, xmin=0.2, ymax=0.3, xmax=0.4, page_number=1)


def complete_record(page_number: int = 1, **overrides) -> ExtractedRecord:
    values = dict(
        page_number=page_number,
        first_name=FieldValue(value="Dana", confidence="high", box=BOX),
        last_name=FieldValue(value="Levi", confidence="high"),
        id_number=FieldValue(value=VALID_ID, confidence="high", raw_value="18"),
        received_date=FieldValue(value="2026-03-01", confidence="high"),
        sender=FieldValue(value="סורוקה", confidence="high"),
        central_numbers=[CentralNumberItem(value="000123", confidence="high", source="sticker", box=BOX)],
        commitment_numbers=[CommitmentNumberItem(value="0070", confidence="medium", label_type="commitment")],
    )
    values.update(overrides)
    return ExtractedRecord(**values)


def incomplete_record(page_number: int = 1) -> ExtractedRecord:
    return complete_record(
        page_number,
        last_name=FieldValue(),
        requires_manual_review=True,
        review_reasons=["last_name_missing"],
    )


@pytest.fixture
def service(db_session):
    return RecordService(db_session)


def count_records(session) -> int:
    return session.scalar(select(func.count()).select_from(RecordRow))


def edits_for(session, record_id) -> list[RecordEditRow]:
    return list(session.scalars(select(RecordEditRow).where(RecordEditRow.record_id == record_id)))


def patch(**fields) -> RecordUpdateSchema:
    return RecordUpdateSchema(**fields)


# --- saving --------------------------------------------------------------------


def test_save_records_persists_batch_and_returns_ids_in_order(service, db_session):
    ids = service.save_records([complete_record(1), incomplete_record(2)], "doc-a")

    assert len(ids) == 2 and len(set(ids)) == 2
    assert count_records(db_session) == 2
    first, second = service.get_records_by_ids(ids)
    assert (first.page_number, second.page_number) == (1, 2)
    assert first.document_id == "doc-a"
    assert first.status == "pending_review"
    assert second.requires_manual_review is True
    assert second.review_reasons == ["last_name_missing"]


def test_leading_zeros_boxes_and_confidence_survive_round_trip(service):
    [record_id] = service.save_records([complete_record()], "doc-a")
    stored = service.get_record(record_id)

    assert stored.id_number.value == "000000018"
    assert stored.id_number.raw_value == "18"
    assert stored.central_numbers[0].value == "000123"
    assert stored.central_numbers[0].box == BOX
    assert stored.commitment_numbers[0].value == "0070"
    assert stored.commitment_numbers[0].confidence == "medium"
    assert stored.first_name.box == BOX


def test_failed_insert_rolls_back_entire_batch(service, db_session):
    service.save_records([complete_record(1)], "doc-existing")

    with pytest.raises(IntegrityError):
        service.save_records([complete_record(1), complete_record(2), complete_record(2)], "doc-new")

    assert count_records(db_session) == 1
    assert service.get_records(sender=None)[1] == 1


def test_error_before_commit_rolls_back(service, db_session, monkeypatch):
    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db_session, "commit", boom)
    with pytest.raises(RuntimeError):
        service.save_records([complete_record(1), complete_record(2)], "doc-a")
    monkeypatch.undo()

    assert count_records(db_session) == 0


def test_resaving_same_document_is_idempotent(service, db_session):
    first_ids = service.save_records([complete_record(1), complete_record(2)], "doc-a")
    second_ids = service.save_records([complete_record(1), complete_record(2)], "doc-a")

    assert first_ids == second_ids
    assert count_records(db_session) == 2


def test_retry_does_not_overwrite_corrections_or_decisions(service):
    edited_id, approved_id = service.save_records([incomplete_record(1), complete_record(2)], "doc-a")
    service.update_record(edited_id, patch(first_name="Corrected"))
    service.update_record(approved_id, patch(status="approved"))

    retry = [complete_record(1, first_name=FieldValue(value="Model", confidence="high")), incomplete_record(2)]
    assert service.save_records(retry, "doc-a") == [edited_id, approved_id]

    assert service.get_record(edited_id).first_name.value == "Corrected"
    approved = service.get_record(approved_id)
    assert approved.status == "approved"
    assert approved.last_name.value == "Levi"


def test_retry_replaces_unedited_failed_page_only(service):
    failed_id, edited_failed_id = service.save_records(
        [build_failed_record(1, "provider_timeout"), build_failed_record(2, "provider_timeout")], "doc-a"
    )
    service.update_record(edited_failed_id, patch(first_name="Manual"))

    ids = service.save_records([complete_record(1), complete_record(2)], "doc-a")

    assert ids == [failed_id, edited_failed_id]
    replaced = service.get_record(failed_id)
    assert replaced.status == "pending_review"
    assert replaced.first_name.value == "Dana"
    kept = service.get_record(edited_failed_id)
    assert kept.status == "extraction_failed"
    assert kept.first_name.value == "Manual"


def test_schema_initialization_keeps_existing_data(tmp_path):
    url = f"sqlite:///{tmp_path / 'nested' / 'app.db'}"
    engine = create_db_engine(url)
    init_db(engine)
    with Session(engine) as session:
        [record_id] = RecordService(session).save_records([complete_record()], "doc-a")
    engine.dispose()

    restarted = create_db_engine(url)
    init_db(restarted)
    with Session(restarted) as session:
        assert RecordService(session).get_record(record_id).id_number.value == VALID_ID
    restarted.dispose()


# --- status transitions --------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("pending_review", "approved"),
        ("pending_review", "rejected"),
        ("extraction_failed", "pending_review"),
        ("extraction_failed", "rejected"),
        ("approved", "pending_review"),
        ("rejected", "pending_review"),
    ],
)
def test_allowed_transitions(service, db_session, start, target):
    [record_id] = service.save_records([complete_record()], "doc-a")
    db_session.execute(update(RecordRow).where(RecordRow.id == record_id).values(status=start))
    db_session.commit()

    assert service.update_record(record_id, patch(status=target)).status == target


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("extraction_failed", "approved"),
        ("approved", "rejected"),
        ("rejected", "approved"),
    ],
)
def test_disallowed_transitions(service, db_session, start, target):
    [record_id] = service.save_records([complete_record()], "doc-a")
    db_session.execute(update(RecordRow).where(RecordRow.id == record_id).values(status=start))
    db_session.commit()

    with pytest.raises(InvalidTransitionError):
        service.update_record(record_id, patch(status=target))
    assert service.get_record(record_id).status == start


def test_failed_record_cannot_be_approved_even_with_corrections(service):
    [record_id] = service.save_records([build_failed_record(1, "malformed_response")], "doc-a")
    with pytest.raises(InvalidTransitionError):
        service.update_record(
            record_id,
            patch(
                first_name="Dana",
                last_name="Levi",
                id_number=VALID_ID,
                received_date="01/03/2026",
                sender="סורוקה",
                status="approved",
            ),
        )
    assert service.get_record(record_id).first_name.value is None


def test_approval_requires_valid_mandatory_fields(service):
    [record_id] = service.save_records([incomplete_record()], "doc-a")
    with pytest.raises(InvalidTransitionError, match="last_name_missing"):
        service.update_record(record_id, patch(status="approved"))

    approved = service.update_record(record_id, patch(last_name="Levi", status="approved"))
    assert approved.status == "approved"
    assert approved.requires_manual_review is False


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("id_number", "123456789", "id_number_invalid"),
        ("sender", "Unknown clinic", "sender_unrecognized"),
        ("received_date", None, "received_date_missing"),
    ],
)
def test_approval_rejects_invalid_values(service, field, value, issue):
    [record_id] = service.save_records([complete_record()], "doc-a")
    service.update_record(record_id, patch(**{field: value}))
    with pytest.raises(InvalidTransitionError, match=issue):
        service.update_record(record_id, patch(status="approved"))


def test_approved_record_must_be_reopened_before_editing(service):
    [record_id] = service.save_records([complete_record()], "doc-a")
    service.update_record(record_id, patch(status="approved"))

    with pytest.raises(InvalidTransitionError):
        service.update_record(record_id, patch(first_name="Other"))

    reopened = service.update_record(record_id, patch(first_name="Other", status="pending_review"))
    assert reopened.status == "pending_review"
    assert reopened.first_name.value == "Other"


def test_reopening_failed_record_reassesses_review_reasons(service):
    [record_id] = service.save_records([build_failed_record(1, "provider_error")], "doc-a")
    reopened = service.update_record(
        record_id, patch(first_name="Dana", last_name="Levi", id_number="18", status="pending_review")
    )

    assert "provider_error" not in reopened.review_reasons
    assert set(reopened.review_reasons) == {"received_date_missing", "sender_missing"}
    assert reopened.requires_manual_review is True


# --- manual corrections --------------------------------------------------------


def test_omitted_fields_are_unchanged_and_null_clears(service):
    [record_id] = service.save_records([complete_record()], "doc-a")
    updated = service.update_record(record_id, patch(last_name=None))

    assert updated.first_name.value == "Dana"
    assert updated.last_name.value is None
    assert updated.last_name.confidence == "low"
    assert updated.review_reasons == ["last_name_missing"]
    assert updated.requires_manual_review is True


def test_null_list_clears_items_and_omitted_list_is_kept(service):
    [record_id] = service.save_records([complete_record()], "doc-a")
    updated = service.update_record(record_id, RecordUpdateSchema.model_validate({"commitment_numbers": None}))

    assert updated.commitment_numbers == []
    assert updated.central_numbers[0].value == "000123"


def test_empty_patch_changes_nothing(service, db_session):
    [record_id] = service.save_records([complete_record()], "doc-a")
    before = service.get_record(record_id)

    assert service.update_record(record_id, patch()) == before
    assert edits_for(db_session, record_id) == []


def test_valid_correction_clears_field_reasons_and_review_flag(service):
    record = complete_record(
        id_number=FieldValue(value="123456789", confidence="low"),
        requires_manual_review=True,
        review_reasons=["id_number_invalid"],
    )
    [record_id] = service.save_records([record], "doc-a")

    updated = service.update_record(record_id, patch(id_number="18"))

    assert updated.id_number.value == VALID_ID
    assert updated.id_number.confidence == "high"
    assert updated.review_reasons == []
    assert updated.requires_manual_review is False


def test_invalid_id_correction_is_kept_but_flagged(service):
    [record_id] = service.save_records([complete_record()], "doc-a")
    updated = service.update_record(record_id, patch(id_number="123456789"))

    assert updated.id_number.value == "123456789"
    assert updated.id_number.confidence == "low"
    assert updated.review_reasons == ["id_number_invalid"]
    assert updated.requires_manual_review is True


def test_correction_normalizes_date_and_sender_alias(service):
    [record_id] = service.save_records([incomplete_record()], "doc-a")
    updated = service.update_record(record_id, patch(received_date="05.04.2026", sender="תל השומר"))

    assert updated.received_date.value == "2026-04-05"
    assert updated.sender.value == "שיבא (תל השומר)"
    assert updated.sender.raw_value == "תל השומר"


def test_invalid_date_correction_is_rejected_without_changes(service):
    [record_id] = service.save_records([complete_record()], "doc-a")
    with pytest.raises(InvalidRecordUpdateError):
        service.update_record(record_id, patch(received_date="31/02/2026", first_name="Other"))
    assert service.get_record(record_id).first_name.value == "Dana"


def test_corrected_items_keep_boxes_for_unchanged_values(service):
    [record_id] = service.save_records([complete_record()], "doc-a")
    updated = service.update_record(
        record_id,
        patch(central_numbers=[{"value": "000123", "source": "sticker"}, {"value": "045678"}]),
    )

    assert [item.value for item in updated.central_numbers] == ["000123", "045678"]
    assert updated.central_numbers[0].box == BOX
    assert updated.central_numbers[1].box is None
    assert all(item.confidence == "high" for item in updated.central_numbers)


def test_edits_are_recorded_in_history(service, db_session):
    [record_id] = service.save_records([complete_record()], "doc-a")
    service.update_record(record_id, patch(first_name="Dina", status="approved"))

    history = {edit.field: edit for edit in edits_for(db_session, record_id)}
    assert set(history) == {"first_name", "status"}
    assert history["first_name"].old_value["value"] == "Dana"
    assert history["first_name"].new_value["value"] == "Dina"
    assert (history["status"].old_value, history["status"].new_value) == ("pending_review", "approved")


def test_updated_at_advances_and_created_at_is_kept(service):
    [record_id] = service.save_records([complete_record()], "doc-a")
    before = service.get_record(record_id)
    after = service.update_record(record_id, patch(first_name="Dina"))

    assert after.created_at == before.created_at
    assert after.updated_at >= before.updated_at


def test_unknown_record_raises_not_found(service):
    with pytest.raises(RecordNotFoundError):
        service.get_record("missing")
    with pytest.raises(RecordNotFoundError):
        service.update_record("missing", patch(first_name="x"))


# --- querying ------------------------------------------------------------------

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture
def seeded(service, db_session):
    """Five records created one day apart (r0 oldest)."""
    records = [
        complete_record(1),
        complete_record(2, sender=FieldValue(value="מכבי", confidence="high")),
        incomplete_record(3),
        build_failed_record(4, "provider_error"),
        complete_record(5, sender=FieldValue(value="מכבי", confidence="high")),
    ]
    ids = service.save_records(records, "doc-a")
    for day, record_id in enumerate(ids):
        db_session.execute(
            update(RecordRow).where(RecordRow.id == record_id).values(created_at=BASE_TIME + timedelta(days=day))
        )
    db_session.commit()
    service.update_record(ids[4], patch(status="approved"))
    return ids


def ids_of(result) -> list[str]:
    return [record.id for record in result[0]]


def test_records_are_ordered_newest_first(service, seeded):
    items, total = service.get_records()
    assert total == 5
    assert [record.id for record in items] == list(reversed(seeded))


def test_filter_by_status(service, seeded):
    assert ids_of(service.get_records(status=RecordStatus.extraction_failed)) == [seeded[3]]
    assert ids_of(service.get_records(status=RecordStatus.approved)) == [seeded[4]]


def test_filter_by_review_flag(service, seeded):
    assert ids_of(service.get_records(requires_manual_review=True)) == [seeded[3], seeded[2]]


def test_filter_by_created_range(service, seeded):
    result = service.get_records(created_from=BASE_TIME + timedelta(days=1), created_to=BASE_TIME + timedelta(days=3))
    assert ids_of(result) == [seeded[3], seeded[2], seeded[1]]


def test_timezone_aware_range_is_converted_to_utc(service, seeded):
    israel = timezone(timedelta(hours=2))
    result = service.get_records(created_from=datetime(2026, 1, 5, 14, 0, tzinfo=israel))
    assert ids_of(result) == [seeded[4]]


def test_combined_filters(service, seeded):
    result = service.get_records(
        sender="מכבי",
        status=RecordStatus.pending_review,
        created_from=BASE_TIME,
        created_to=BASE_TIME + timedelta(days=4),
    )
    assert ids_of(result) == [seeded[1]]
    assert service.get_records(sender="מכבי", requires_manual_review=True) == ([], 0)


@pytest.mark.parametrize(
    ("limit", "offset", "expected_positions"),
    [
        (2, 0, [4, 3]),
        (2, 4, [0]),
        (5, 0, [4, 3, 2, 1, 0]),
        (10, 5, []),
        (1, 100, []),
    ],
)
def test_pagination_boundaries(service, seeded, limit, offset, expected_positions):
    items, total = service.get_records(limit=limit, offset=offset)
    assert total == 5
    assert [record.id for record in items] == [seeded[i] for i in expected_positions]
