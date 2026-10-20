import asyncio
import json

import pytest

from backend.app.services.extractor_service import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    ExtractionError,
    extract_record_from_image,
)
from backend.tests.fakes import JPEG_BYTES, PNG_BYTES, FakeVisionProvider, failing_provider, valid_payload


def _extract(provider, image=PNG_BYTES, page_number=1):
    return asyncio.run(extract_record_from_image(image, page_number, provider=provider))


def test_successful_extraction_maps_to_validated_record():
    provider = FakeVisionProvider()
    record = _extract(provider, page_number=3)

    assert record.page_number == 3
    assert record.first_name.value == "דנה"
    assert record.first_name.box.page_number == 3
    assert record.id_number.value == "123456782"
    assert record.received_date.value == "2024-03-05"
    assert record.sender.value == "כללית"
    assert record.central_numbers[0].value == "123456"
    assert record.requires_manual_review is False


def test_provider_receives_image_prompt_and_schema():
    provider = FakeVisionProvider()
    _extract(provider, image=JPEG_BYTES)

    call = provider.calls[0]
    assert call["image_bytes"] == JPEG_BYTES
    assert call["mime_type"] == "image/jpeg"
    assert call["system_prompt"] == SYSTEM_PROMPT
    assert call["response_schema"] is RESPONSE_SCHEMA


def test_code_fenced_json_is_accepted():
    provider = FakeVisionProvider(response="```json\n" + json.dumps(valid_payload()) + "\n```")
    assert _extract(provider).first_name.value == "דנה"


def test_validation_runs_on_model_output():
    payload = valid_payload()
    payload["id_number"]["value"] = "123456789"
    record = _extract(FakeVisionProvider(response=json.dumps(payload)))

    assert record.requires_manual_review is True
    assert "id_number_invalid" in record.review_reasons


@pytest.mark.parametrize(
    "response, message",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("{not json", "malformed"),
        ('{"first_name": ', "malformed"),
        ("[]", "not a JSON object"),
        ('"text"', "not a JSON object"),
        ("{}", "missing fields"),
        (json.dumps({k: v for k, v in valid_payload().items() if k != "sender"}), "sender"),
    ],
)
def test_malformed_or_empty_responses_raise(response, message):
    with pytest.raises(ExtractionError, match=message):
        _extract(FakeVisionProvider(response=response))


def test_provider_failure_raises_extraction_error():
    with pytest.raises(ExtractionError, match="Vision provider failed"):
        _extract(failing_provider())


@pytest.mark.parametrize("image", [b"", b"GIF89a....", b"%PDF-1.7"])
def test_empty_or_unsupported_images_are_rejected_before_calling_provider(image):
    provider = FakeVisionProvider()
    with pytest.raises(ExtractionError):
        _extract(provider, image=image)
    assert provider.calls == []
