"""Persistence and review lifecycle of extracted records."""

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import RecordEditRow, RecordRow
from ..schemas import (
    CentralNumberItem,
    CommitmentNumberItem,
    ConfidenceLevel,
    ExtractedRecord,
    RecordResponse,
    RecordStatus,
    RecordUpdateSchema,
)
from .validator_service import (
    MANUAL_TEXT_FIELDS,
    InvalidManualValueError,
    mandatory_field_issues,
    validate_manual_field,
)

# Allowed manual status changes. extraction_failed can never go straight to approved.
ALLOWED_TRANSITIONS: dict[RecordStatus, frozenset[RecordStatus]] = {
    RecordStatus.pending_review: frozenset({RecordStatus.approved, RecordStatus.rejected}),
    RecordStatus.extraction_failed: frozenset({RecordStatus.pending_review, RecordStatus.rejected}),
    RecordStatus.approved: frozenset({RecordStatus.pending_review}),
    RecordStatus.rejected: frozenset({RecordStatus.pending_review}),
}


class RecordNotFoundError(LookupError):
    pass


class InvalidTransitionError(ValueError):
    """The requested status change or edit is not allowed in the record's current state."""


class InvalidRecordUpdateError(ValueError):
    """A supplied value is invalid (e.g. an unparseable date)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_utc_naive(value: datetime) -> datetime:
    """Interpret naive datetimes as UTC; convert aware ones to naive UTC for storage/queries."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso_utc(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat()


def _belongs_to(reason: str, field: str) -> bool:
    return reason == field or reason.startswith(f"{field}_") or reason.startswith(f"{field}[")


def _requires_review(status: RecordStatus, reasons: list[str]) -> bool:
    if status is RecordStatus.extraction_failed:
        return True
    if status in (RecordStatus.approved, RecordStatus.rejected):
        return False
    return bool(reasons)


class RecordService:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- writes --------------------------------------------------------------

    def save_records(self, records: Sequence[ExtractedRecord], document_id: str) -> list[str]:
        """Persist one document's page records atomically and return their IDs in input order.

        Idempotent per (document_id, page_number): an existing page is kept unchanged unless
        it is an `extraction_failed` page with no manual edits, in which case the new result
        replaces it. Manual corrections and review decisions are never overwritten. Any
        error rolls back the whole batch.
        """
        now = _utcnow()
        try:
            existing = {
                row.page_number: row
                for row in self._session.scalars(select(RecordRow).where(RecordRow.document_id == document_id))
            }
            ids = []
            for record in records:
                row = existing.get(record.page_number)
                if row is None:
                    row = RecordRow(id=str(uuid.uuid4()), document_id=document_id, created_at=now)
                    self._apply(row, record, now)
                    self._session.add(row)
                elif row.status == RecordStatus.extraction_failed.value and not row.edits:
                    self._apply(row, record, now)
                ids.append(row.id)
            self._session.flush()
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return ids

    def update_record(self, record_id: str, patch_data: RecordUpdateSchema) -> RecordResponse:
        row = self._get_row(record_id)
        record = self._to_extracted(row)
        current = RecordStatus(row.status)
        supplied = patch_data.model_fields_set
        edited_fields = supplied - {"status"}
        target = RecordStatus(patch_data.status) if "status" in supplied else current

        if edited_fields and current is RecordStatus.approved and target is not RecordStatus.pending_review:
            raise InvalidTransitionError("Approved records must be reopened (status=pending_review) before editing")
        if target is not current and target not in ALLOWED_TRANSITIONS[current]:
            raise InvalidTransitionError(f"Cannot change status from {current.value} to {target.value}")

        reasons = list(record.review_reasons)
        updates: dict[str, Any] = {}
        for name in MANUAL_TEXT_FIELDS:
            if name not in edited_fields:
                continue
            try:
                field, field_reasons = validate_manual_field(
                    name, getattr(patch_data, name), getattr(record, name).box
                )
            except InvalidManualValueError as exc:
                raise InvalidRecordUpdateError(str(exc)) from exc
            reasons = [r for r in reasons if not _belongs_to(r, name)] + field_reasons
            updates[name] = field

        if "central_numbers" in edited_fields:
            boxes = {item.value: item.box for item in record.central_numbers}
            updates["central_numbers"] = [
                CentralNumberItem(
                    value=item.value, confidence=ConfidenceLevel.high, source=item.source, box=boxes.get(item.value)
                )
                for item in patch_data.central_numbers or []
            ]
            reasons = [r for r in reasons if not _belongs_to(r, "central_numbers")]
        if "commitment_numbers" in edited_fields:
            boxes = {item.value: item.box for item in record.commitment_numbers}
            updates["commitment_numbers"] = [
                CommitmentNumberItem(
                    value=item.value,
                    confidence=ConfidenceLevel.high,
                    label_type=item.label_type,
                    box=boxes.get(item.value),
                )
                for item in patch_data.commitment_numbers or []
            ]
            reasons = [r for r in reasons if not _belongs_to(r, "commitment_numbers")]

        updated = record.model_copy(update=updates)
        if current is RecordStatus.extraction_failed and target is not RecordStatus.extraction_failed:
            # Technical failure codes no longer apply; reassess the page on its current values.
            reasons = mandatory_field_issues(updated)
        if target is RecordStatus.approved:
            issues = mandatory_field_issues(updated)
            if issues:
                raise InvalidTransitionError(f"Cannot approve record: {', '.join(issues)}")

        updated = ExtractedRecord.model_validate(
            {
                **updated.model_dump(),
                "review_reasons": reasons,
                "status": target.value,
                "requires_manual_review": _requires_review(target, reasons),
            }
        )

        now = _utcnow()
        for name in sorted(updates):
            self._log_edit(row, name, _dump(getattr(record, name)), _dump(getattr(updated, name)), now)
        if target is not current:
            self._log_edit(row, "status", current.value, target.value, now)
        if updates or target is not current:
            self._apply(row, updated, now)
            self._session.commit()
        return self._to_response(row)

    # --- reads ---------------------------------------------------------------

    def get_record(self, record_id: str) -> RecordResponse:
        return self._to_response(self._get_row(record_id))

    def get_records_by_ids(self, record_ids: Sequence[str]) -> list[RecordResponse]:
        rows = {row.id: row for row in self._session.scalars(select(RecordRow).where(RecordRow.id.in_(record_ids)))}
        return [self._to_response(rows[record_id]) for record_id in record_ids]

    def get_records(
        self,
        *,
        status: Optional[RecordStatus] = None,
        sender: Optional[str] = None,
        requires_manual_review: Optional[bool] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RecordResponse], int]:
        """Filter (all criteria combined with AND), order newest first, and paginate."""
        conditions = []
        if status is not None:
            conditions.append(RecordRow.status == status.value)
        if sender is not None:
            conditions.append(RecordRow.sender == sender)
        if requires_manual_review is not None:
            conditions.append(RecordRow.requires_manual_review == requires_manual_review)
        if created_from is not None:
            conditions.append(RecordRow.created_at >= _to_utc_naive(created_from))
        if created_to is not None:
            conditions.append(RecordRow.created_at <= _to_utc_naive(created_to))

        total = self._session.scalar(select(func.count()).select_from(RecordRow).where(*conditions)) or 0
        rows = self._session.scalars(
            select(RecordRow)
            .where(*conditions)
            .order_by(
                RecordRow.created_at.desc(), RecordRow.document_id, RecordRow.page_number, RecordRow.id
            )
            .limit(limit)
            .offset(offset)
        )
        return [self._to_response(row) for row in rows], total

    # --- helpers -------------------------------------------------------------

    def _get_row(self, record_id: str) -> RecordRow:
        row = self._session.get(RecordRow, record_id)
        if row is None:
            raise RecordNotFoundError(record_id)
        return row

    def _log_edit(self, row: RecordRow, field: str, old: Any, new: Any, now: datetime) -> None:
        row.edits.append(RecordEditRow(field=field, old_value=old, new_value=new, edited_at=now))

    @staticmethod
    def _apply(row: RecordRow, record: ExtractedRecord, now: datetime) -> None:
        row.page_number = record.page_number
        row.status = RecordStatus(record.status).value
        row.requires_manual_review = record.requires_manual_review
        row.sender = record.sender.value
        row.id_number = record.id_number.value
        row.received_date = record.received_date.value
        row.data = record.model_dump(mode="json")
        row.updated_at = now

    @staticmethod
    def _to_extracted(row: RecordRow) -> ExtractedRecord:
        return ExtractedRecord.model_validate(
            {
                **row.data,
                "page_number": row.page_number,
                "status": row.status,
                "requires_manual_review": row.requires_manual_review,
            }
        )

    def _to_response(self, row: RecordRow) -> RecordResponse:
        return RecordResponse.model_validate(
            {
                **self._to_extracted(row).model_dump(),
                "id": row.id,
                "document_id": row.document_id,
                "created_at": _iso_utc(row.created_at),
                "updated_at": _iso_utc(row.updated_at),
            }
        )


def _dump(value: Any) -> Any:
    if isinstance(value, list):
        return [item.model_dump(mode="json") for item in value]
    return value.model_dump(mode="json")
