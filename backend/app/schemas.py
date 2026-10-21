from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfidenceLevel(str, Enum):
    """high: clear and unambiguous; medium: readable with some uncertainty;
    low: missing, unreadable, ambiguous, or invalid."""

    high = "high"
    medium = "medium"
    low = "low"


class BoundingBox(BaseModel):
    """Normalized (0.0-1.0) region of a field on a page."""

    ymin: float = Field(ge=0.0, le=1.0)
    xmin: float = Field(ge=0.0, le=1.0)
    ymax: float = Field(ge=0.0, le=1.0)
    xmax: float = Field(ge=0.0, le=1.0)
    page_number: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _check_ordering(self) -> "BoundingBox":
        if self.ymin > self.ymax:
            raise ValueError("ymin must be less than or equal to ymax")
        if self.xmin > self.xmax:
            raise ValueError("xmin must be less than or equal to xmax")
        return self


class FieldValue(BaseModel):
    value: Optional[str] = None
    confidence: ConfidenceLevel = ConfidenceLevel.low
    box: Optional[BoundingBox] = None
    raw_value: Optional[str] = None  # text as read, before normalization


class CentralNumberItem(BaseModel):
    value: str
    confidence: ConfidenceLevel
    source: Optional[str] = None  # e.g. "sticker", "handwritten"
    box: Optional[BoundingBox] = None


class CommitmentNumberItem(BaseModel):
    value: str
    confidence: ConfidenceLevel
    label_type: Optional[str] = None  # e.g. "commitment", "order", "sample"
    box: Optional[BoundingBox] = None


class ExtractedRecord(BaseModel):
    page_number: int = Field(default=1, ge=1)
    image_path: Optional[str] = None
    first_name: FieldValue
    last_name: FieldValue
    id_number: FieldValue
    central_numbers: list[CentralNumberItem] = []
    received_date: FieldValue  # ISO YYYY-MM-DD or None
    sender: FieldValue
    commitment_numbers: list[CommitmentNumberItem] = []
    requires_manual_review: bool = False
    review_reasons: list[str] = []
    status: str = "pending_review"


class RecordStatus(str, Enum):
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    extraction_failed = "extraction_failed"


class RecordResponse(ExtractedRecord):
    id: str
    document_id: str
    created_at: str
    updated_at: str


class RecordListResponse(BaseModel):
    items: list[RecordResponse]
    total: int
    limit: int
    offset: int


def _strip_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip() or None


def _strip_required(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("value cannot be blank")
    return stripped


class CentralNumberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    source: Optional[str] = None

    _strip_value = field_validator("value")(_strip_required)
    _strip = field_validator("source")(_strip_or_none)


class CommitmentNumberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    label_type: Optional[str] = None

    _strip_value = field_validator("value")(_strip_required)
    _strip = field_validator("label_type")(_strip_or_none)


class RecordUpdateSchema(BaseModel):
    """Manual correction of a record. Only editable domain fields are accepted.

    Omitted fields are left unchanged; an explicit null clears a field (or empties a list).
    Strings only: identifiers sent as JSON numbers are rejected so leading zeros are kept.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    id_number: Optional[str] = None
    received_date: Optional[str] = None
    sender: Optional[str] = None
    central_numbers: Optional[list[CentralNumberUpdate]] = None
    commitment_numbers: Optional[list[CommitmentNumberUpdate]] = None
    status: Optional[Literal["pending_review", "approved", "rejected"]] = None

    _strip = field_validator("first_name", "last_name", "id_number", "received_date", "sender")(_strip_or_none)

    @model_validator(mode="after")
    def _status_not_null(self) -> "RecordUpdateSchema":
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        return self
