"""Ground-truth manifest for the extraction benchmark.

The manifest lists documents (paths relative to the manifest file) and, per page, the
verified value and location of each field. Only fields present on a page are scored, so a
manifest can cover a subset of fields. An explicit `"value": null` means the field is
absent or unreadable on the form, and the extractor is expected to return nothing.

Values are given in the form the API returns: ISO dates, 9-digit ID numbers, and the
canonical sender name from the closed authority list.

Pages drafted from extractor output (`run --draft-ground-truth`) carry `"verified": false`
and are skipped when scoring until a person has checked and corrected them.
"""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..app.schemas import BoundingBox

SCALAR_FIELDS = ("first_name", "last_name", "id_number", "received_date", "sender")
LIST_FIELDS = ("central_numbers", "commitment_numbers")


class TruthBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ymin: float = Field(ge=0.0, le=1.0)
    xmin: float = Field(ge=0.0, le=1.0)
    ymax: float = Field(ge=0.0, le=1.0)
    xmax: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _positive_area(self) -> "TruthBox":
        if self.ymin >= self.ymax or self.xmin >= self.xmax:
            raise ValueError("box must have ymin < ymax and xmin < xmax")
        return self

    @classmethod
    def from_box(cls, box: Optional[BoundingBox]) -> Optional["TruthBox"]:
        if box is None or box.ymin >= box.ymax or box.xmin >= box.xmax:
            return None
        return cls(ymin=box.ymin, xmin=box.xmin, ymax=box.ymax, xmax=box.xmax)


class TruthField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Optional[str] = None
    box: Optional[TruthBox] = None

    @model_validator(mode="after")
    def _box_needs_value(self) -> "TruthField":
        if self.value is None and self.box is not None:
            raise ValueError("an absent field cannot have a box")
        return self


class TruthItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    box: Optional[TruthBox] = None


class TruthPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    verified: bool = True
    first_name: Optional[TruthField] = None
    last_name: Optional[TruthField] = None
    id_number: Optional[TruthField] = None
    received_date: Optional[TruthField] = None
    sender: Optional[TruthField] = None
    central_numbers: Optional[list[TruthItem]] = None
    commitment_numbers: Optional[list[TruthItem]] = None


class TruthDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    # Label used in reports instead of the file name, which may contain patient details.
    id: Optional[str] = None
    pages: list[TruthPage]

    @model_validator(mode="after")
    def _unique_pages(self) -> "TruthDocument":
        numbers = [page.page_number for page in self.pages]
        if len(numbers) != len(set(numbers)):
            raise ValueError(f"duplicate page numbers in {self.file}")
        return self


class GroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[TruthDocument] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_labels(self) -> "GroundTruth":
        labels = [document.id for document in self.documents if document.id is not None]
        if len(labels) != len(set(labels)):
            raise ValueError("document ids must be unique")
        return self


def load_ground_truth(path: Path) -> GroundTruth:
    return GroundTruth.model_validate_json(path.read_bytes())
