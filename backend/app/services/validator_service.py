"""Domain normalization and validation of extracted commitment records."""

import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, ValidationError

from ..constants import (
    SENDER_AMBIGUITY_MARGIN,
    SENDER_MATCH_THRESHOLD,
    SENDING_ENTITIES,
    SENDING_ENTITY_ALIASES,
)
from ..schemas import (
    BoundingBox,
    CentralNumberItem,
    CommitmentNumberItem,
    ConfidenceLevel,
    ExtractedRecord,
    FieldValue,
)

ISRAELI_ID_LENGTH = 9
# Fields read digit by digit from handwriting, eligible for one targeted re-read of their crop.
RECHECK_FIELDS = ("id_number", "received_date_stamp", "received_date_handwritten")
# Each re-read is a vision API call; cap per page so a doubtful page cannot
# multiply the request's quota cost beyond this bound.
MAX_RECHECKS_PER_PAGE = 2
# The model writes this in place of each digit it cannot read.
UNREADABLE_MARK = "?"
MAX_ALTERNATIVES = 5
_ID_SEPARATORS = re.compile(r"[\s-]")

_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%d.%m.%y",
    "%d-%m-%y",
    "%Y-%m-%d",
)
_QUOTE_TRANSLATION = str.maketrans({"״": '"', "”": '"', "“": '"', "׳": "'", "’": "'"})
_CONFIDENCE_RANK = {
    ConfidenceLevel.low: 0,
    ConfidenceLevel.medium: 1,
    ConfidenceLevel.high: 2,
}


def validate_israeli_id(id_str: str | None) -> tuple[str | None, bool]:
    """Return the ID zero-padded to 9 digits and whether its check digit is valid.

    Cleaning removes only whitespace and hyphens. Padding is applied only to a cleaned
    value of 1-9 ASCII digits; anything else (non-numeric, longer than 9 digits) is
    rejected and returned stripped but otherwise unchanged. Digits are never inferred.
    """
    if id_str is None:
        return None, False
    stripped = id_str.strip()
    if not stripped:
        return None, False
    candidate = _ID_SEPARATORS.sub("", stripped)
    if not (candidate.isascii() and candidate.isdigit()) or len(candidate) > ISRAELI_ID_LENGTH:
        return stripped, False

    normalized = candidate.zfill(ISRAELI_ID_LENGTH)
    if int(normalized) == 0:
        return normalized, False

    total = 0
    for index, digit in enumerate(normalized):
        product = int(digit) * (1 if index % 2 == 0 else 2)
        total += product - 9 if product > 9 else product
    return normalized, total % 10 == 0


def normalize_date(date_str: str | None) -> str | None:
    """Parse DD/MM/YYYY, DD.MM.YYYY, DD-MM-YYYY (or 2-digit years) and YYYY-MM-DD to ISO YYYY-MM-DD.

    Two-digit years follow Python's strptime pivot (00-68 -> 20xx, 69-99 -> 19xx).
    Returns None for impossible calendar dates or unparseable input.
    """
    if date_str is None:
        return None
    candidate = date_str.strip()
    if not candidate:
        return None
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("''", '"').translate(_QUOTE_TRANSLATION)
    return " ".join(text.split())


_SENDER_ALIASES: dict[str, tuple[str, ...]] = {
    canonical: tuple(
        {_normalize_text(alias) for alias in (canonical, *SENDING_ENTITY_ALIASES.get(canonical, ()))}
    )
    for canonical in SENDING_ENTITIES
}


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _best_fuzzy_ratio(text: str, alias: str) -> float:
    tokens = text.split()
    width = len(alias.split())
    windows = [" ".join(tokens[i : i + width]) for i in range(max(len(tokens) - width + 1, 1))]
    return max(SequenceMatcher(None, window, alias).ratio() for window in windows)


def normalize_sender(raw_sender: str | None) -> tuple[str | None, ConfidenceLevel]:
    """Match a sender against SENDING_ENTITIES.

    Exact or whole-phrase match -> (canonical, high); unique fuzzy match -> (canonical, medium);
    unknown or ambiguous -> (raw sender, low).
    """
    if raw_sender is None:
        return None, ConfidenceLevel.low
    text = _normalize_text(raw_sender)
    if not text:
        return None, ConfidenceLevel.low

    exact = {c for c, aliases in _SENDER_ALIASES.items() if text in aliases}
    if len(exact) == 1:
        return exact.pop(), ConfidenceLevel.high

    contained = {
        c for c, aliases in _SENDER_ALIASES.items() if any(_contains_phrase(text, a) for a in aliases)
    }
    if len(contained) == 1:
        return contained.pop(), ConfidenceLevel.high
    if contained:
        return raw_sender.strip(), ConfidenceLevel.low

    scores = {
        c: max(_best_fuzzy_ratio(text, a) for a in aliases) for c, aliases in _SENDER_ALIASES.items()
    }
    match = select_fuzzy_match(scores)
    if match is not None:
        return match, ConfidenceLevel.medium
    return raw_sender.strip(), ConfidenceLevel.low


def select_fuzzy_match(scores: Mapping[str, float]) -> str | None:
    """Return the best candidate only if it clears the threshold and the ambiguity margin."""
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_name, best_score = ranked[0]
    if best_score < SENDER_MATCH_THRESHOLD:
        return None
    if len(ranked) > 1 and best_score - ranked[1][1] < SENDER_AMBIGUITY_MARGIN:
        return None
    return best_name


def _min_confidence(*levels: ConfidenceLevel) -> ConfidenceLevel:
    return min(levels, key=_CONFIDENCE_RANK.__getitem__)


def _optional_str(raw: Any) -> str | None:
    return (raw.strip() or None) if isinstance(raw, str) else None


def _parse_confidence(raw: Any) -> ConfidenceLevel:
    try:
        return ConfidenceLevel(raw)
    except ValueError:
        return ConfidenceLevel.low


def _parse_box(raw: Any, page_number: int, name: str, reasons: list[str]) -> BoundingBox | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        reasons.append(f"{name}_invalid_box")
        return None
    coords = {key: raw.get(key) for key in ("ymin", "xmin", "ymax", "xmax")}
    try:
        # Strict: strings, booleans, NaN and out-of-range/misordered values are rejected,
        # never coerced or corrected.
        return BoundingBox.model_validate({**coords, "page_number": page_number}, strict=True)
    except ValidationError:
        reasons.append(f"{name}_invalid_box")
        return None


def _parse_text(raw: Any, name: str, page_number: int, reasons: list[str]) -> FieldValue:
    """Parse an untrusted {value, confidence, box} object; missing values become low/None.

    A missing box on an unavailable field is normal. A missing box on an extracted value
    is flagged as `<name>_box_missing`; an invalid box anywhere is dropped and flagged
    as `<name>_invalid_box`. A valid box on an unreadable field is kept so reviewers can
    see where the field is.
    """
    if raw is None:
        return FieldValue()
    if not isinstance(raw, Mapping):
        reasons.append(f"{name}_invalid_type")
        return FieldValue()

    value = raw.get("value")
    if value is not None and not isinstance(value, str):
        reasons.append(f"{name}_invalid_type")
        return FieldValue()
    value = (value.strip() or None) if value else None
    box = _parse_box(raw.get("box"), page_number, name, reasons)
    if value is None:
        return FieldValue(box=box)
    if raw.get("box") is None:
        reasons.append(f"{name}_box_missing")

    return FieldValue(value=value, confidence=_parse_confidence(raw.get("confidence")), box=box)


class FieldReading(BaseModel):
    """One reading of a field: the most likely text and any other plausible complete readings."""

    value: str | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.low
    alternatives: list[str] = []


# Result of the targeted re-read per field name; None means the re-read was attempted but failed.
FieldRechecks = Mapping[str, FieldReading | None]


def parse_reading(raw: Any) -> FieldReading:
    """Parse an untrusted {value, confidence, alternatives} object; anything malformed is dropped."""
    if not isinstance(raw, Mapping):
        return FieldReading()
    value = _optional_str(raw.get("value"))
    alternatives: list[str] = []
    raw_alternatives = raw.get("alternatives")
    if isinstance(raw_alternatives, list):
        for item in raw_alternatives:
            text = _optional_str(item)
            if text is not None and text != value and text not in alternatives:
                alternatives.append(text)
    return FieldReading(
        value=value,
        confidence=_parse_confidence(raw.get("confidence")),
        alternatives=alternatives[:MAX_ALTERNATIVES] if value is not None else [],
    )


def _is_valid_reading(name: str, value: str) -> bool:
    if name == "id_number":
        return validate_israeli_id(value)[1]
    return normalize_date(value) is not None


def _reading_key(name: str, value: str) -> str:
    """Comparison key: separators and date formatting do not make two readings different."""
    if name == "id_number":
        return _ID_SEPARATORS.sub("", value.strip())
    return normalize_date(value) or " ".join(value.split())


def _has_doubt(name: str, reading: FieldReading) -> bool:
    if reading.value is None:
        return True
    return (
        reading.confidence is ConfidenceLevel.low
        or bool(reading.alternatives)
        or UNREADABLE_MARK in reading.value
        or not _is_valid_reading(name, reading.value)
    )


def recheck_boxes(raw: Mapping[str, Any], page_number: int) -> dict[str, BoundingBox]:
    """Located ID/date fields whose reading is missing, invalid, ambiguous, partial, or low-confidence.

    A field without a valid box cannot be cropped and is left to manual review as is.
    """
    boxes = {}
    for name in RECHECK_FIELDS:
        if len(boxes) >= MAX_RECHECKS_PER_PAGE:
            break
        field = raw.get(name)
        if not isinstance(field, Mapping):
            continue
        box = _parse_box(field.get("box"), page_number, name, [])
        if box is not None and _has_doubt(name, parse_reading(field)):
            boxes[name] = box
    return boxes


def _distinct_readings(name: str, readings: list[str | None]) -> list[str]:
    distinct: dict[str, str] = {}
    for reading in readings:
        if reading is not None:
            distinct.setdefault(_reading_key(name, reading), reading)
    return list(distinct.values())


def _unresolved(field: FieldValue, candidates: list[str], reason: str, reasons: list[str]) -> FieldValue:
    """Conflicting readings: no value is chosen; the readings are kept for the reviewer."""
    reasons.append(reason)
    return FieldValue(
        confidence=ConfidenceLevel.low, box=field.box, raw_value=field.value, candidates=candidates
    )


def _reconcile_readings(
    name: str, field: FieldValue, raw: Any, rechecks: FieldRechecks, reasons: list[str]
) -> FieldValue:
    """Apply ambiguity, partial-readability, and targeted re-read evidence to a digit field.

    Agreement between the page reading and a confident, unambiguous re-read of the crop
    resolves model-reported doubt (confidence becomes medium, never high). Any disagreement
    leaves the field without a value and with every distinct reading as a candidate.
    """
    if field.value is None and name not in rechecks:
        return field
    alternatives = parse_reading(raw).alternatives if field.value is not None else []
    if field.value is not None and UNREADABLE_MARK in field.value:
        reasons.append(f"{name}_partially_unreadable")

    recheck = rechecks.get(name)
    if name in rechecks and recheck is None:
        reasons.append(f"{name}_recheck_failed")
    if recheck is not None:
        agrees = (
            field.value is not None
            and recheck.value is not None
            and recheck.confidence is not ConfidenceLevel.low
            and not recheck.alternatives
            and UNREADABLE_MARK not in field.value + recheck.value
            and _reading_key(name, field.value) == _reading_key(name, recheck.value)
        )
        if agrees:
            if _is_valid_reading(name, field.value):
                return field.model_copy(update={"confidence": ConfidenceLevel.medium})
            return field
        readings = _distinct_readings(name, [field.value, *alternatives, recheck.value, *recheck.alternatives])
        if len(readings) > 1 or (field.value is None and readings):
            return _unresolved(field, readings, f"{name}_recheck_disagrees", reasons)

    if alternatives:
        return _unresolved(field, [field.value, *alternatives], f"{name}_ambiguous", reasons)
    return field


def _finalize_simple(field: FieldValue, name: str, reasons: list[str]) -> FieldValue:
    if field.value is None:
        reasons.append(f"{name}_missing")
    elif field.confidence is ConfidenceLevel.low:
        reasons.append(f"{name}_low_confidence")
    return field


def _finalize_id(field: FieldValue, reasons: list[str]) -> FieldValue:
    if field.value is None:
        reasons.append("id_number_missing")
        return field
    normalized, valid = validate_israeli_id(field.value)
    confidence = field.confidence
    if not valid:
        reasons.append("id_number_invalid")
        confidence = ConfidenceLevel.low
    elif confidence is ConfidenceLevel.low:
        reasons.append("id_number_low_confidence")
    return field.model_copy(
        update={"value": normalized, "confidence": confidence, "raw_value": field.value}
    )


def _finalize_received_date(
    stamp: FieldValue, handwritten: FieldValue, reasons: list[str]
) -> FieldValue:
    """Prefer a valid "התקבל" stamp date, then a valid handwritten date."""
    stamp_iso = normalize_date(stamp.value)
    handwritten_iso = normalize_date(handwritten.value)
    if stamp.value is not None and stamp_iso is None:
        reasons.append("received_date_stamp_invalid")
    if handwritten.value is not None and handwritten_iso is None:
        reasons.append("received_date_handwritten_invalid")

    for source, iso in ((stamp, stamp_iso), (handwritten, handwritten_iso)):
        if iso is not None:
            if source.confidence is ConfidenceLevel.low:
                reasons.append("received_date_low_confidence")
            return source.model_copy(update={"value": iso, "raw_value": source.value})

    if stamp.value is None and handwritten.value is None:
        reasons.append("received_date_missing")
    return FieldValue(
        raw_value=stamp.value or stamp.raw_value or handwritten.value or handwritten.raw_value,
        box=stamp.box or handwritten.box,
        candidates=stamp.candidates or handwritten.candidates,
    )


def _finalize_sender(field: FieldValue, reasons: list[str]) -> FieldValue:
    if field.value is None:
        reasons.append("sender_missing")
        return field
    canonical, match_confidence = normalize_sender(field.value)
    if match_confidence is ConfidenceLevel.low:
        reasons.append("sender_unrecognized")
    confidence = _min_confidence(field.confidence, match_confidence)
    if confidence is ConfidenceLevel.low and match_confidence is not ConfidenceLevel.low:
        reasons.append("sender_low_confidence")
    return field.model_copy(
        update={"value": canonical, "confidence": confidence, "raw_value": field.value}
    )


def _parse_items(raw: Any, name: str, page_number: int, reasons: list[str]) -> list[dict[str, Any]]:
    """Return validated item dicts (value, confidence, box, raw item) for list fields."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        reasons.append(f"{name}_invalid_type")
        return []

    items = []
    for index, item in enumerate(raw):
        item_name = f"{name}[{index}]"
        value = item.get("value") if isinstance(item, Mapping) else None
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"{item_name}_invalid")
            continue
        confidence = _parse_confidence(item.get("confidence"))
        if confidence is ConfidenceLevel.low:
            reasons.append(f"{item_name}_low_confidence")
        if item.get("box") is None:
            reasons.append(f"{item_name}_box_missing")
        items.append(
            {
                "value": value.strip(),
                "confidence": confidence,
                "box": _parse_box(item.get("box"), page_number, item_name, reasons),
                "raw": item,
            }
        )
    return items


def _optional_str(raw: Any) -> str | None:
    return (raw.strip() or None) if isinstance(raw, str) else None


def finalize_record(
    raw: Mapping[str, Any], page_number: int, rechecks: FieldRechecks | None = None
) -> ExtractedRecord:
    """Validate untrusted model output for one page and build an ExtractedRecord.

    `rechecks` holds targeted re-reads of doubtful ID/date fields (see `recheck_boxes`).

    Any missing, invalid, or low-confidence data is recorded in review_reasons and sets
    requires_manual_review; the record is still a successfully processed page. A payload
    that is not a JSON object is a technical failure and raises TypeError.
    """
    if not isinstance(raw, Mapping):
        raise TypeError("Extraction payload must be a JSON object")
    reasons: list[str] = []

    def text(name: str) -> FieldValue:
        return _parse_text(raw.get(name), name, page_number, reasons)

    first_name = _finalize_simple(text("first_name"), "first_name", reasons)
    last_name = _finalize_simple(text("last_name"), "last_name", reasons)
    rechecks = rechecks or {}

    def digits(name: str) -> FieldValue:
        return _reconcile_readings(name, text(name), raw.get(name), rechecks, reasons)

    id_number = _finalize_id(digits("id_number"), reasons)
    received_date = _finalize_received_date(
        digits("received_date_stamp"), digits("received_date_handwritten"), reasons
    )
    sender = _finalize_sender(text("sender"), reasons)

    central_numbers = [
        CentralNumberItem(
            value=item["value"],
            confidence=item["confidence"],
            source=_optional_str(item["raw"].get("source")),
            box=item["box"],
        )
        for item in _parse_items(raw.get("central_numbers"), "central_numbers", page_number, reasons)
    ]
    commitment_numbers = [
        CommitmentNumberItem(
            value=item["value"],
            confidence=item["confidence"],
            label_type=_optional_str(item["raw"].get("label_type")),
            box=item["box"],
        )
        for item in _parse_items(
            raw.get("commitment_numbers"), "commitment_numbers", page_number, reasons
        )
    ]

    if raw.get("requires_manual_review") is True:
        reasons.append("flagged_by_model")

    return ExtractedRecord(
        page_number=page_number,
        first_name=first_name,
        last_name=last_name,
        id_number=id_number,
        received_date=received_date,
        sender=sender,
        central_numbers=central_numbers,
        commitment_numbers=commitment_numbers,
        requires_manual_review=bool(reasons),
        review_reasons=reasons,
    )


MANUAL_TEXT_FIELDS = ("first_name", "last_name", "id_number", "received_date", "sender")


class InvalidManualValueError(ValueError):
    """A manually supplied value cannot be stored (e.g. an unparseable date)."""


def validate_manual_field(
    name: str, value: str | None, box: BoundingBox | None = None
) -> tuple[FieldValue, list[str]]:
    """Validate a human correction with the same rules as extraction.

    Returns the field (keeping its original box) and the review reasons it still raises.
    A valid correction gets high confidence (fuzzy sender matches keep medium).
    """
    if name not in MANUAL_TEXT_FIELDS:
        raise ValueError(f"{name} is not a manually editable text field")
    if value is None or not value.strip():
        return FieldValue(box=box), [f"{name}_missing"]
    value = value.strip()

    if name == "id_number":
        normalized, valid = validate_israeli_id(value)
        if not valid:
            return FieldValue(value=normalized, confidence=ConfidenceLevel.low, box=box, raw_value=value), [
                "id_number_invalid"
            ]
        return FieldValue(value=normalized, confidence=ConfidenceLevel.high, box=box, raw_value=value), []

    if name == "received_date":
        iso = normalize_date(value)
        if iso is None:
            raise InvalidManualValueError(f"received_date {value!r} is not a valid date")
        return FieldValue(value=iso, confidence=ConfidenceLevel.high, box=box, raw_value=value), []

    if name == "sender":
        canonical, match_confidence = normalize_sender(value)
        if match_confidence is ConfidenceLevel.low:
            return FieldValue(value=canonical, confidence=ConfidenceLevel.low, box=box, raw_value=value), [
                "sender_unrecognized"
            ]
        return FieldValue(value=canonical, confidence=match_confidence, box=box, raw_value=value), []

    return FieldValue(value=value, confidence=ConfidenceLevel.high, box=box), []


def mandatory_field_issues(record: ExtractedRecord) -> list[str]:
    """Reasons a record cannot be approved: mandatory fields missing or failing validation."""
    issues = []
    for name in ("first_name", "last_name"):
        if getattr(record, name).value is None:
            issues.append(f"{name}_missing")

    id_value = record.id_number.value
    if id_value is None:
        issues.append("id_number_missing")
    elif validate_israeli_id(id_value) != (id_value, True):
        issues.append("id_number_invalid")

    date_value = record.received_date.value
    if date_value is None:
        issues.append("received_date_missing")
    elif normalize_date(date_value) != date_value:
        issues.append("received_date_invalid")

    sender_value = record.sender.value
    if sender_value is None:
        issues.append("sender_missing")
    elif sender_value not in SENDING_ENTITIES:
        issues.append("sender_unrecognized")
    return issues
