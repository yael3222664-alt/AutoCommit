from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


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


class RecordResponse(ExtractedRecord):
    id: str
    created_at: str
