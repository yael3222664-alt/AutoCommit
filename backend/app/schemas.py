from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ConfidenceLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class FieldValue(BaseModel):
    value: Optional[str] = None
    confidence: ConfidenceLevel = ConfidenceLevel.low


class CentralNumberItem(BaseModel):
    value: str
    confidence: ConfidenceLevel
    source: Optional[str] = None  # e.g. "sticker", "handwritten"


class CommitmentNumberItem(BaseModel):
    value: str
    confidence: ConfidenceLevel
    label_type: Optional[str] = None  # e.g. "commitment", "order", "sample"


class ExtractedRecord(BaseModel):
    first_name: FieldValue
    last_name: FieldValue
    id_number: FieldValue
    central_numbers: list[CentralNumberItem] = []
    received_date: FieldValue  # ISO YYYY-MM-DD or None
    sender: FieldValue
    commitment_numbers: list[CommitmentNumberItem] = []
    requires_manual_review: bool = False
    status: str = "pending_review"


class RecordResponse(ExtractedRecord):
    id: str
    created_at: str
