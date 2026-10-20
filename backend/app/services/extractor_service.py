"""Vision extraction of one page image into a validated ExtractedRecord."""

import json
import re
from typing import Any, Optional

from ..config import get_settings
from ..schemas import ExtractedRecord
from .ingestion_service import detect_image_mime_type
from .validator_service import finalize_record
from .vision_provider import VisionProvider, VisionProviderError, get_vision_provider


class ExtractionError(Exception):
    """Raised when a page cannot be extracted; no record is produced."""


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
3. Visual grounding: for every value you return, provide "box" with normalized \
coordinates ymin, xmin, ymax, xmax as floats between 0.0 and 1.0, relative to the image \
height (y) and width (x), with ymin <= ymax and xmin <= xmax. The box must tightly \
enclose the text the value was read from.
4. Transcribe values exactly as written. Do not translate, reformat, correct, or complete them.
5. Patient identity: "first_name" and "last_name" of the patient, and "id_number": the \
patient's Israeli ID number (תעודת זהות, up to 9 digits) exactly as written.
6. Central numbers: find every sticker labeled "מרכזי" and every handwritten identifier \
written next to such a sticker (typically 6 digits). Return each occurrence as a separate \
item in "central_numbers" with "source" set to "sticker" or "handwritten". Never merge or \
drop occurrences.
7. Receipt date: report both candidates and do not choose between them.
   - "received_date_stamp": the date inside the "התקבל" (received) stamp of the central laboratory.
   - "received_date_handwritten": a handwritten date written next to or below the "מרכזי" sticker.
   Transcribe each date exactly as written (for example 05/03/2024).
8. Commitment / reference numbers: return every number labeled טופס 17, אסמכתא, \
מספר דרישה (Maccabi), or דגימה (Leumit / hospital) as an item in "commitment_numbers" \
with "label_type": "commitment" for טופס 17, "reference" for אסמכתא, "order" for \
מספר דרישה, and "sample" for דגימה.
9. Sender: "sender" is the sending organization (HMO, hospital, or medical institution) \
exactly as written on the form.
10. Set "requires_manual_review" to true if any field is missing, unreadable, or \
ambiguous, or if anything on the page is unclear.
"""

USER_PROMPT = "Extract the commitment form fields from this page image following the rules."

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

RESPONSE_SCHEMA: dict[str, Any] = _object(
    {
        "first_name": _TEXT_FIELD_SCHEMA,
        "last_name": _TEXT_FIELD_SCHEMA,
        "id_number": _TEXT_FIELD_SCHEMA,
        "received_date_stamp": _TEXT_FIELD_SCHEMA,
        "received_date_handwritten": _TEXT_FIELD_SCHEMA,
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

_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _parse_payload(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip() if raw_text else ""
    fenced = _CODE_FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    if not text:
        raise ExtractionError("Vision provider returned an empty response")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionError("Vision provider returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise ExtractionError("Vision provider response is not a JSON object")
    missing = [key for key in RESPONSE_SCHEMA["required"] if key not in payload]
    if missing:
        raise ExtractionError(f"Vision provider response is missing fields: {', '.join(missing)}")
    return payload


async def extract_record_from_image(
    image_bytes: bytes,
    page_number: int = 1,
    provider: Optional[VisionProvider] = None,
) -> ExtractedRecord:
    """Extract and validate one page image. Raises ExtractionError on any failure."""
    if not image_bytes:
        raise ExtractionError("Image is empty")
    mime_type = detect_image_mime_type(image_bytes)
    if mime_type is None:
        raise ExtractionError("Image format is not supported; expected PNG or JPEG")

    if provider is None:
        provider = get_vision_provider(get_settings())
    try:
        raw_text = await provider.generate_json(
            image_bytes=image_bytes,
            mime_type=mime_type,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT,
            response_schema=RESPONSE_SCHEMA,
        )
    except VisionProviderError as exc:
        raise ExtractionError(f"Vision provider failed: {exc}") from exc

    return finalize_record(_parse_payload(raw_text), page_number)
