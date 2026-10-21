"""SQLAlchemy ORM models for persisted commitment records."""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ..schemas import RecordStatus

_STATUS_VALUES = ", ".join(f"'{status.value}'" for status in RecordStatus)


class Base(DeclarativeBase):
    pass


class RecordRow(Base):
    """One page of an uploaded document.

    `data` holds the full ExtractedRecord JSON (fields, boxes, confidences, item lists).
    Identifier and filter columns are denormalized from it as strings so leading zeros
    are never lost. Timestamps are stored as naive UTC.
    """

    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_records_document_page"),
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="ck_records_status"),
        CheckConstraint("page_number >= 1", name="ck_records_page_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    requires_manual_review: Mapped[bool] = mapped_column(index=True)
    sender: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    id_number: Mapped[Optional[str]] = mapped_column(String(32))
    received_date: Mapped[Optional[str]] = mapped_column(String(10))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    edits: Mapped[list["RecordEditRow"]] = relationship(
        back_populates="record", cascade="all, delete-orphan", order_by="RecordEditRow.id"
    )


class RecordEditRow(Base):
    """Audit trail of manual changes to a record (field values and status)."""

    __tablename__ = "record_edits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    edited_at: Mapped[datetime] = mapped_column(DateTime)

    record: Mapped[RecordRow] = relationship(back_populates="edits")
