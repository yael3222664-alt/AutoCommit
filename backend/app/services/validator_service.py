"""Domain normalization and validation of extracted commitment records."""

import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Optional

from pydantic import ValidationError

from ..constants import SENDING_ENTITIES
from ..schemas import (
    BoundingBox,
    CentralNumberItem,
    CommitmentNumberItem,
    ConfidenceLevel,
    ExtractedRecord,
    FieldValue,
)

ISRAELI_ID_LENGTH = 9
SENDER_FUZZY_THRESHOLD = 0.8

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


def validate_israeli_id(id_str: Optional[str]) -> tuple[Optional[str], bool]:
    """Return the ID zero-padded to 9 digits and whether its check digit is valid.

    Values that are not 1-9 ASCII digits are returned stripped but otherwise unchanged.
    """
    if id_str is None:
        return None, False
    candidate = id_str.strip()
    if not candidate:
        return None, False
    if not (candidate.isascii() and candidate.isdigit()) or len(candidate) > ISRAELI_ID_LENGTH:
        return candidate, False

    normalized = candidate.zfill(ISRAELI_ID_LENGTH)
    if int(normalized) == 0:
        return normalized, False

    total = 0
    for index, digit in enumerate(normalized):
        product = int(digit) * (1 if index % 2 == 0 else 2)
        total += product - 9 if product > 9 else product
    return normalized, total % 10 == 0


def normalize_date(date_str: Optional[str]) -> Optional[str]:
    """Parse DD/MM/YYYY, DD.MM.YYYY, DD-MM-YYYY (or 2-digit years) and ISO dates to YYYY-MM-DD."""
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
    canonical: tuple({_normalize_text(alias) for alias in (canonical, *aliases)})
    for canonical, aliases in SENDING_ENTITIES.items()
}


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _best_fuzzy_ratio(text: str, alias: str) -> float:
    tokens = text.split()
    width = len(alias.split())
    windows = [" ".join(tokens[i : i + width]) for i in range(max(len(tokens) - width + 1, 1))]
    return max(SequenceMatcher(None, window, alias).ratio() for window in windows)


def normalize_sender(raw_sender: Optional[str]) -> tuple[Optional[str], ConfidenceLevel]:
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

    fuzzy = {
        c
        for c, aliases in _SENDER_ALIASES.items()
        if max(_best_fuzzy_ratio(text, a) for a in aliases) >= SENDER_FUZZY_THRESHOLD
    }
    if len(fuzzy) == 1:
        return fuzzy.pop(), ConfidenceLevel.medium
    return raw_sender.strip(), ConfidenceLevel.low


def _min_confidence(*levels: ConfidenceLevel) -> ConfidenceLevel:
    return min(levels, key=_CONFIDENCE_RANK.__getitem__)


def _parse_confidence(raw: Any) -> ConfidenceLevel:
    try:
        return ConfidenceLevel(raw)
    except ValueError:
        return ConfidenceLevel.low


def _parse_box(raw: Any, page_number: int, name: str, reasons: list[str]) -> Optional[BoundingBox]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        reasons.append(f"{name}_invalid_box")
        return None
    try:
        return BoundingBox(
            ymin=raw.get("ymin"),
            xmin=raw.get("xmin"),
            ymax=raw.get("ymax"),
            xmax=raw.get("xmax"),
            page_number=page_number,
        )
    except ValidationError:
        reasons.append(f"{name}_invalid_box")
        return None


def _parse_text(raw: Any, name: str, page_number: int, reasons: list[str]) -> FieldValue:
    """Parse an untrusted {value, confidence, box} object; missing values become low/None."""
    if raw is None:
        return FieldValue()
    if not isinstance(raw, Mapping):
        reasons.append(f"{name}_invalid_type")
        return FieldValue()

    value = raw.get("value")
    if value is not None and not isinstance(value, str):
        reasons.append(f"{name}_invalid_type")
        return FieldValue()
    value = value.strip() if value else None
    if value is None:
        return FieldValue()

    return FieldValue(
        value=value,
        confidence=_parse_confidence(raw.get("confidence")),
        box=_parse_box(raw.get("box"), page_number, name, reasons),
    )


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
    return FieldValue(raw_value=stamp.value or handwritten.value)


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
        items.append(
            {
                "value": value.strip(),
                "confidence": confidence,
                "box": _parse_box(item.get("box"), page_number, item_name, reasons),
                "raw": item,
            }
        )
    return items


def _optional_str(raw: Any) -> Optional[str]:
    return (raw.strip() or None) if isinstance(raw, str) else None


def finalize_record(raw: Mapping[str, Any], page_number: int) -> ExtractedRecord:
    """Validate untrusted model output for one page and build an ExtractedRecord.

    Any missing, invalid, or low-confidence data is recorded in review_reasons and sets
    requires_manual_review.
    """
    reasons: list[str] = []

    def text(name: str) -> FieldValue:
        return _parse_text(raw.get(name), name, page_number, reasons)

    first_name = _finalize_simple(text("first_name"), "first_name", reasons)
    last_name = _finalize_simple(text("last_name"), "last_name", reasons)
    id_number = _finalize_id(text("id_number"), reasons)
    received_date = _finalize_received_date(
        text("received_date_stamp"), text("received_date_handwritten"), reasons
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
