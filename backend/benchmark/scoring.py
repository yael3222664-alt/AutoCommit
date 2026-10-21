"""Compares extracted records with ground truth (pure functions, no I/O)."""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from statistics import mean, median
from typing import Optional

from ..app.schemas import BoundingBox, ExtractedRecord, RecordStatus
from .ground_truth import LIST_FIELDS, SCALAR_FIELDS, TruthBox, TruthItem, TruthPage

DEFAULT_IOU_THRESHOLD = 0.5

_QUOTES = str.maketrans({"״": '"', "”": '"', "“": '"', "׳": "'", "’": "'"})
_NUMBER_SEPARATORS = re.compile(r"[\s\-/.]")
_NUMERIC_FIELDS = {"id_number", *LIST_FIELDS}


class Outcome(str, Enum):
    correct = "correct"
    wrong = "wrong"  # a different value was extracted
    missed = "missed"  # a value exists on the form, nothing was extracted
    hallucinated = "hallucinated"  # nothing on the form, a value was extracted
    correct_absent = "correct_absent"  # nothing on the form, nothing extracted


class BoxOutcome(str, Enum):
    aligned = "aligned"  # IoU at or above the threshold
    misaligned = "misaligned"
    missing = "missing"  # value extracted, no usable box returned
    wrong_page = "wrong_page"
    not_scored = "not_scored"  # no truth box, or nothing to compare


@dataclass(frozen=True)
class FieldResult:
    field: str
    outcome: Outcome
    box_outcome: BoxOutcome = BoxOutcome.not_scored
    iou: Optional[float] = None
    expected: Optional[str] = None
    extracted: Optional[str] = None


@dataclass(frozen=True)
class PageResult:
    document: str
    page_number: int
    status: str
    extraction_failed: bool
    requires_manual_review: bool
    review_reasons: list[str]
    fields: list[FieldResult]
    seconds: Optional[float] = None


def normalize_value(field_name: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(unicodedata.normalize("NFKC", value).translate(_QUOTES).split())
    if field_name in _NUMERIC_FIELDS:
        text = _NUMBER_SEPARATORS.sub("", text)
    return text or None


def iou(a: TruthBox, b: TruthBox) -> float:
    height = min(a.ymax, b.ymax) - max(a.ymin, b.ymin)
    width = min(a.xmax, b.xmax) - max(a.xmin, b.xmin)
    if height <= 0 or width <= 0:
        return 0.0
    intersection = height * width
    union = (a.ymax - a.ymin) * (a.xmax - a.xmin) + (b.ymax - b.ymin) * (b.xmax - b.xmin) - intersection
    return intersection / union


def _score_box(
    truth: Optional[TruthBox], box: Optional[BoundingBox], page_number: int, threshold: float
) -> tuple[BoxOutcome, Optional[float]]:
    if truth is None:
        return BoxOutcome.not_scored, None
    extracted = TruthBox.from_box(box)
    if extracted is None:
        return BoxOutcome.missing, None
    if box is not None and box.page_number != page_number:
        return BoxOutcome.wrong_page, None
    overlap = iou(truth, extracted)
    return (BoxOutcome.aligned if overlap >= threshold else BoxOutcome.misaligned), overlap


def _score_scalar(name: str, truth, record: ExtractedRecord, threshold: float) -> FieldResult:
    extracted = getattr(record, name)
    expected_value = normalize_value(name, truth.value)
    extracted_value = normalize_value(name, extracted.value)
    if expected_value is None:
        outcome = Outcome.correct_absent if extracted_value is None else Outcome.hallucinated
    elif extracted_value is None:
        outcome = Outcome.missed
    else:
        outcome = Outcome.correct if expected_value == extracted_value else Outcome.wrong
    box_outcome, overlap = BoxOutcome.not_scored, None
    if extracted_value is not None and expected_value is not None:
        box_outcome, overlap = _score_box(truth.box, extracted.box, record.page_number, threshold)
    return FieldResult(name, outcome, box_outcome, overlap, truth.value, extracted.value)


def _score_items(name: str, truth: list[TruthItem], record: ExtractedRecord, threshold: float) -> list[FieldResult]:
    remaining = list(getattr(record, name))
    results = []
    for item in truth:
        expected_value = normalize_value(name, item.value)
        match = next((e for e in remaining if normalize_value(name, e.value) == expected_value), None)
        if match is None:
            results.append(FieldResult(name, Outcome.missed, expected=item.value))
            continue
        remaining.remove(match)
        box_outcome, overlap = _score_box(item.box, match.box, record.page_number, threshold)
        results.append(FieldResult(name, Outcome.correct, box_outcome, overlap, item.value, match.value))
    # Unmatched extracted items: a wrong reading of a real number or an invented one.
    kind = Outcome.wrong if truth else Outcome.hallucinated
    results.extend(FieldResult(name, kind, extracted=extra.value) for extra in remaining)
    if not truth and not remaining:
        results.append(FieldResult(name, Outcome.correct_absent))
    return results


def score_page(
    document: str,
    truth: TruthPage,
    record: ExtractedRecord,
    threshold: float = DEFAULT_IOU_THRESHOLD,
    seconds: Optional[float] = None,
) -> PageResult:
    results: list[FieldResult] = []
    for name in SCALAR_FIELDS:
        if getattr(truth, name) is not None:
            results.append(_score_scalar(name, getattr(truth, name), record, threshold))
    for name in LIST_FIELDS:
        if getattr(truth, name) is not None:
            results.extend(_score_items(name, getattr(truth, name), record, threshold))
    return PageResult(
        document=document,
        page_number=truth.page_number,
        status=record.status.value,
        extraction_failed=record.status is RecordStatus.extraction_failed,
        requires_manual_review=record.requires_manual_review,
        review_reasons=list(record.review_reasons),
        fields=results,
        seconds=seconds,
    )


@dataclass
class FieldSummary:
    outcomes: Counter = field(default_factory=Counter)
    boxes: Counter = field(default_factory=Counter)
    ious: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        total = sum(self.outcomes.values())
        present = sum(self.outcomes[o] for o in (Outcome.correct, Outcome.wrong, Outcome.missed))
        scored_boxes = sum(self.boxes[b] for b in BoxOutcome if b is not BoxOutcome.not_scored)
        return {
            "total": total,
            "accuracy": _ratio(self.outcomes[Outcome.correct] + self.outcomes[Outcome.correct_absent], total),
            "recall": _ratio(self.outcomes[Outcome.correct], present),
            "outcomes": {o.value: self.outcomes[o] for o in Outcome},
            "boxes": {
                "scored": scored_boxes,
                "aligned_rate": _ratio(self.boxes[BoxOutcome.aligned], scored_boxes),
                "mean_iou": round(mean(self.ious), 3) if self.ious else None,
                "median_iou": round(median(self.ious), 3) if self.ious else None,
                **{b.value: self.boxes[b] for b in BoxOutcome if b is not BoxOutcome.not_scored},
            },
        }


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 3) if denominator else None


def summarize(pages: list[PageResult], threshold: float = DEFAULT_IOU_THRESHOLD) -> dict:
    per_field: dict[str, FieldSummary] = {}
    overall = FieldSummary()
    reasons: Counter = Counter()
    for page in pages:
        reasons.update(page.review_reasons)
        for result in page.fields:
            for summary in (per_field.setdefault(result.field, FieldSummary()), overall):
                summary.outcomes[result.outcome] += 1
                summary.boxes[result.box_outcome] += 1
                if result.iou is not None:
                    summary.ious.append(result.iou)
    timings = [page.seconds for page in pages if page.seconds is not None]
    return {
        "iou_threshold": threshold,
        "pages": len(pages),
        "extraction_failed_pages": sum(page.extraction_failed for page in pages),
        "pages_flagged_for_review": sum(page.requires_manual_review for page in pages),
        "pages_fully_correct": sum(
            all(r.outcome in (Outcome.correct, Outcome.correct_absent) for r in page.fields) for page in pages
        ),
        "seconds_per_page": {
            "mean": round(mean(timings), 2) if timings else None,
            "max": round(max(timings), 2) if timings else None,
        },
        "overall": overall.as_dict(),
        "fields": {name: per_field[name].as_dict() for name in (*SCALAR_FIELDS, *LIST_FIELDS) if name in per_field},
        "review_reasons": dict(reasons.most_common()),
    }
