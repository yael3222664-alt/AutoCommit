"""Vision extraction of one page image into a validated ExtractedRecord."""

import asyncio
import json
import logging
import re
from typing import Any

from ..config import get_settings
from ..constants import SENDING_ENTITIES
from ..schemas import ExtractedRecord
from .image_crop import ImageCropError, crop_regions
from .ingestion_service import detect_image_mime_type
from .validator_service import (
    FieldReading,
    finalize_record,
    parse_reading,
    recheck_boxes,
)
from .vision_provider import (
    VisionProvider,
    VisionProviderError,
    VisionProviderNotConfiguredError,
    VisionProviderTimeoutError,
    get_vision_provider,
)

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Technical failure: the page could not be processed, so no extracted values exist.

    This is distinct from a processed page whose fields are missing or unreadable.
    `code` is a stable, non-sensitive reason: invalid_image, provider_not_configured,
    provider_timeout, provider_error, empty_response, or malformed_response.
    """

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


SYSTEM_PROMPT = """\
You extract data from a single scanned page of an Israeli medical commitment form \
(טופס התחייבות). The page may contain printed text, stamps, printed stickers, and \
handwritten notes, mostly in Hebrew.

Return JSON that matches the response schema exactly. Rules:

1. No hallucinations. Never guess, infer, or fabricate values. If a field is not \
explicitly visible, is missing, ambiguous, or unreadable, set "value" to null, \
"confidence" to "low", and "box" to null, and set "requires_manual_review" to true.
2. Confidence levels:
   - "high": clearly visible, readable, and unambiguous.
   - "medium": readable with some uncertainty.
   - "low": missing, unreadable, ambiguous, or invalid.
3. Visual grounding: for every value you return, provide "box" with coordinates \
ymin, xmin, ymax, xmax as integers from 0 to 1000, relative to the image height (y) and \
width (x), where 0 is the top/left edge and 1000 the bottom/right edge, with ymin <= ymax \
and xmin <= xmax. The box must tightly enclose the text the value was read from.
4. Transcribe values exactly as written. Do not translate, reformat, correct, or complete them.
5. Patient identity: "first_name" and "last_name" of the patient, and "id_number": the \
patient's Israeli ID number (תעודת זהות, up to 9 digits) exactly as written, including any \
leading zeros. Never add, drop, or reorder digits to make it 9 digits long or to satisfy the \
check digit.
6. Central numbers: find every sticker labeled "מרכזי" and every handwritten identifier \
written next to such a sticker (typically 6 digits). Return each occurrence as a separate \
item in "central_numbers" with "source" set to "sticker" or "handwritten". Never merge or \
drop occurrences.
7. Receipt date: report both candidates and do not choose between them.
   - "received_date_stamp": the date inside the "התקבל" (received) stamp of the central laboratory.
   - "received_date_handwritten": a handwritten date written next to or below the "מרכזי" sticker.
   Transcribe each date exactly as written (for example 05/03/2024), including its separators \
and year length. Never swap day and month, complete a partial year, or adjust a date that \
looks impossible; report what is written.
8. Commitment / reference numbers: return every number labeled טופס 17, אסמכתא, \
מספר דרישה (Maccabi), or דגימה (Leumit / hospital) as an item in "commitment_numbers" \
with "label_type": "commitment" for טופס 17, "reference" for אסמכתא, "order" for \
מספר דרישה, and "sample" for דגימה.
9. Sender: "sender" is the sending organization — an HMO (קופת חולים) or a hospital \
(בית חולים). The approved sender list is closed: SENDER_LIST. Report the name exactly \
as written on the form; the validator will match it to the list. Never guess or \
substitute a similar-looking name — if the written sender does not clearly appear on \
the form, set "value" to null, "confidence" to "low", and "requires_manual_review" to \
true.
10. Set "requires_manual_review" to true if any field is missing, unreadable, or \
ambiguous, or if anything on the page is unclear.
11. Handwritten digits ("id_number", "received_date_stamp", "received_date_handwritten"): \
read every digit on its own. Write "?" in place of each digit you cannot read; never complete, \
correct, or guess a digit. If a digit could plausibly be read more than one way (for example \
1/7, 4/9, 5/6, 3/8, 0/6, 2/7), put the most likely reading in "value", every other complete \
reading in "alternatives", and set "confidence" to "low". Otherwise "alternatives" is empty.
12. Evidence before assignment: detect every relevant text region first, then decide which \
field it belongs to by its relationship to a label, sticker, or stamp — never assign a value \
just because it sits near a label. A six-digit number is not automatically a central number, \
a date is not automatically the receipt date, and the receiving laboratory is never the \
sending organization. When more than one field could own the text, set the ambiguous field \
to null and flag it for review.
13. Preserve every occurrence: scan the whole page for every instance of the requested \
numbers and dates; do not stop at the first, do not merge or deduplicate occurrences, and \
keep the two receipt-date candidates independent. An occurrence that is visible but \
unreadable is still an occurrence: report it with a null value and flag it.
14. Separate sources: printed text, stickers, handwriting, and stamps are different sources. \
Never combine characters across sources into one value, never treat one source's text as \
evidence for another field, and never reuse a value from a neighboring field.
15. Verify before answering: before producing the final JSON, re-read each region and \
compare every proposed value character by character against the image — including leading \
zeros, similar-looking digits, separators, and boxes. If the second read disagrees and the \
difference cannot be resolved by clear visual evidence, do not pick the more plausible \
reading: return null and flag the field for manual review.
"""

SYSTEM_PROMPT = SYSTEM_PROMPT.replace("SENDER_LIST", ", ".join(SENDING_ENTITIES))

USER_PROMPT = "Extract the commitment form fields from this page image following the rules."

_RECHECK_RULES = """\
Rules:
1. Read every digit on its own and transcribe it exactly as written, left to right, keeping \
separators and leading zeros. Write "?" in place of each digit you cannot read.
2. Never guess, complete, or correct digits, and never use a check digit or calendar rules to \
choose a digit.
3. If a digit could plausibly be read more than one way (for example 1/7, 4/9, 5/6, 3/8, 0/6, \
2/7), put the most likely reading in "value", every other complete reading in "alternatives", \
and set "confidence" to "low". Otherwise "alternatives" is empty.
4. If no such value is visible in the image, set "value" to null and "confidence" to "low".
5. Confidence: "high" clearly readable, "medium" readable with some uncertainty, "low" \
unreadable or ambiguous.
"""

ID_RECHECK_PROMPT = (
    "The image is a crop of a scanned Israeli medical commitment form around the patient's "
    "Israeli ID number (תעודת זהות, up to 9 digits), often handwritten. Return JSON with the "
    "ID number exactly as written.\n" + _RECHECK_RULES
)
DATE_RECHECK_PROMPT = (
    "The image is a crop of a scanned Israeli medical commitment form around a date (from a "
    "\"התקבל\" stamp or handwritten), written day first. Return JSON with the date exactly as "
    "written (for example 05/03/2024); do not swap day and month or complete the year.\n"
    + _RECHECK_RULES
)
RECHECK_USER_PROMPT = "Read the value in this cropped image following the rules."
_RECHECK_PROMPTS = {
    "id_number": ID_RECHECK_PROMPT,
    "received_date_stamp": DATE_RECHECK_PROMPT,
    "received_date_handwritten": DATE_RECHECK_PROMPT,
}

_CONFIDENCE_SCHEMA = {"type": "string", "enum": ["high", "medium", "low"]}
_BOX_SCHEMA = {
    "type": "object",
    "nullable": True,
    "properties": {
        "ymin": {"type": "number"},
        "xmin": {"type": "number"},
        "ymax": {"type": "number"},
        "xmax": {"type": "number"},
    },
    "required": ["ymin", "xmin", "ymax", "xmax"],
}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties)}


_TEXT_FIELD_SCHEMA = _object(
    {"value": {"type": "string", "nullable": True}, "confidence": _CONFIDENCE_SCHEMA, "box": _BOX_SCHEMA}
)

_ALTERNATIVES_SCHEMA = {"type": "array", "items": {"type": "string"}}
_DIGIT_FIELD_SCHEMA = _object(
    {
        "value": {"type": "string", "nullable": True},
        "confidence": _CONFIDENCE_SCHEMA,
        "box": _BOX_SCHEMA,
        "alternatives": _ALTERNATIVES_SCHEMA,
    }
)
RECHECK_SCHEMA: dict[str, Any] = _object(
    {
        "value": {"type": "string", "nullable": True},
        "confidence": _CONFIDENCE_SCHEMA,
        "alternatives": _ALTERNATIVES_SCHEMA,
    }
)

RESPONSE_SCHEMA: dict[str, Any] = _object(
    {
        "first_name": _TEXT_FIELD_SCHEMA,
        "last_name": _TEXT_FIELD_SCHEMA,
        "id_number": _DIGIT_FIELD_SCHEMA,
        "received_date_stamp": _DIGIT_FIELD_SCHEMA,
        "received_date_handwritten": _DIGIT_FIELD_SCHEMA,
        "sender": _TEXT_FIELD_SCHEMA,
        "central_numbers": {
            "type": "array",
            "items": _object(
                {
                    "value": {"type": "string"},
                    "confidence": _CONFIDENCE_SCHEMA,
                    "source": {"type": "string", "nullable": True, "enum": ["sticker", "handwritten"]},
                    "box": _BOX_SCHEMA,
                }
            ),
        },
        "commitment_numbers": {
            "type": "array",
            "items": _object(
                {
                    "value": {"type": "string"},
                    "confidence": _CONFIDENCE_SCHEMA,
                    "label_type": {
                        "type": "string",
                        "nullable": True,
                        "enum": ["commitment", "reference", "order", "sample"],
                    },
                    "box": _BOX_SCHEMA,
                }
            ),
        },
        "requires_manual_review": {"type": "boolean"},
    }
)

# The model reports boxes on a 0-1000 grid (Gemini's native grounding scale); records use 0.0-1.0.
PROVIDER_BOX_SCALE = 1000
_BOX_KEYS = ("ymin", "xmin", "ymax", "xmax")

_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _parse_payload(raw_text: str) -> dict[str, Any]:
    payload = _parse_json_object(raw_text)
    missing = [key for key in RESPONSE_SCHEMA["required"] if key not in payload]
    if missing:
        raise ExtractionError(
            f"Vision provider response is missing fields: {', '.join(missing)}", "malformed_response"
        )
    return payload


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip() if raw_text else ""
    fenced = _CODE_FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    if not text:
        raise ExtractionError("Vision provider returned an empty response", "empty_response")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionError("Vision provider returned malformed JSON", "malformed_response") from exc
    if not isinstance(payload, dict):
        raise ExtractionError("Vision provider response is not a JSON object", "malformed_response")
    return payload


def _rescale_box(raw: Any) -> Any:
    """Convert numeric coordinates to 0.0-1.0; anything else is left for strict validation to reject."""
    if not isinstance(raw, dict):
        return raw
    return {
        key: value / PROVIDER_BOX_SCALE
        if key in _BOX_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool)
        else value
        for key, value in raw.items()
    }


def _rescale_boxes(payload: dict[str, Any]) -> dict[str, Any]:
    for value in payload.values():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict) and "box" in item:
                item["box"] = _rescale_box(item["box"])
    return payload


async def _recheck_fields(
    provider: VisionProvider, image_bytes: bytes, raw: dict[str, Any], page_number: int
) -> dict[str, FieldReading | None]:
    """One targeted re-read per doubtful ID/date field, on a crop around its located box.

    Never more than one call per field; a failed re-read is recorded as None so the field
    goes to manual review instead of being retried again.
    """
    try:
        crops = await asyncio.to_thread(crop_regions, image_bytes, recheck_boxes(raw, page_number))
    except ImageCropError:
        logger.warning("Field recheck skipped: page %s image could not be decoded", page_number)
        return {}
    rechecks: dict[str, FieldReading | None] = {}
    for name, crop in crops.items():
        try:
            raw_text = await provider.generate_json(
                image_bytes=crop,
                mime_type="image/png",
                system_prompt=_RECHECK_PROMPTS[name],
                user_prompt=RECHECK_USER_PROMPT,
                response_schema=RECHECK_SCHEMA,
            )
            rechecks[name] = parse_reading(_parse_json_object(raw_text))
        except (VisionProviderError, ExtractionError):
            logger.warning("Field recheck failed for %s on page %s", name, page_number)
            rechecks[name] = None
    return rechecks


async def extract_record_from_image(
    image_bytes: bytes,
    page_number: int = 1,
    provider: VisionProvider | None = None,
    recheck_fields: bool = True,
) -> ExtractedRecord:
    """Extract and validate one page image. Raises ExtractionError on any failure."""
    if not image_bytes:
        raise ExtractionError("Image is empty", "invalid_image")
    mime_type = detect_image_mime_type(image_bytes)
    if mime_type is None:
        raise ExtractionError("Image format is not supported; expected PNG or JPEG", "invalid_image")

    try:
        if provider is None:
            provider = get_vision_provider(get_settings())
        raw_text = await provider.generate_json(
            image_bytes=image_bytes,
            mime_type=mime_type,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT,
            response_schema=RESPONSE_SCHEMA,
        )
    except VisionProviderNotConfiguredError as exc:
        raise ExtractionError(f"Vision provider failed: {exc}", "provider_not_configured") from exc
    except VisionProviderTimeoutError as exc:
        raise ExtractionError(f"Vision provider failed: {exc}", "provider_timeout") from exc
    except VisionProviderError as exc:
        raise ExtractionError(f"Vision provider failed: {exc}", "provider_error") from exc

    raw = _rescale_boxes(_parse_payload(raw_text))
    rechecks = await _recheck_fields(provider, image_bytes, raw, page_number) if recheck_fields else {}
    return finalize_record(raw, page_number, rechecks)
