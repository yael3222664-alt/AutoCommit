import asyncio
import json

import pytest

from backend.app.config import Settings
from backend.app.services import extractor_service
from backend.app.services.extractor_service import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    ExtractionError,
    extract_record_from_image,
)
from backend.app.services.vision_provider import VisionProviderError, VisionProviderTimeoutError
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


@pytest.mark.parametrize(
    "response, code",
    [
        ("", "empty_response"),
        ("{bad", "malformed_response"),
        ("[1]", "malformed_response"),
        ("{}", "malformed_response"),
    ],
)
def test_response_failures_have_codes(response, code):
    with pytest.raises(ExtractionError) as exc_info:
        _extract(FakeVisionProvider(response=response))
    assert exc_info.value.code == code


@pytest.mark.parametrize(
    "error, code",
    [
        (VisionProviderError("HTTP 500"), "provider_error"),
        (VisionProviderTimeoutError("timed out"), "provider_timeout"),
    ],
)
def test_provider_failures_have_codes(error, code):
    with pytest.raises(ExtractionError) as exc_info:
        _extract(FakeVisionProvider(error=error))
    assert exc_info.value.code == code


def test_invalid_image_code():
    with pytest.raises(ExtractionError) as exc_info:
        _extract(FakeVisionProvider(), image=b"")
    assert exc_info.value.code == "invalid_image"


def test_missing_provider_configuration_is_a_technical_failure(monkeypatch):
    monkeypatch.setattr(extractor_service, "get_settings", lambda: Settings(_env_file=None, GEMINI_API_KEY=None))
    with pytest.raises(ExtractionError) as exc_info:
        _extract(None)
    assert exc_info.value.code == "provider_not_configured"
