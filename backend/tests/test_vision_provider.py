import asyncio
import base64
import json

import httpx
import pytest

from backend.app.config import Settings
from backend.app.services.vision_provider import (
    GeminiVisionProvider,
    VisionProviderError,
    VisionProviderNotConfiguredError,
    get_vision_provider,
)

SCHEMA = {"type": "object", "properties": {"value": {"type": "string", "nullable": True}}}


def _gemini_response(text='{"ok": true}', finish_reason="STOP"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish_reason}]}


def _call(handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = GeminiVisionProvider(
                api_key="test-key",
                model="test-model",
                base_url="https://example.test/v1beta",
                timeout_seconds=5,
                client=client,
            )
            return await provider.generate_json(
                image_bytes=b"img",
                mime_type="image/png",
                system_prompt="sys",
                user_prompt="user",
                response_schema=SCHEMA,
            )

    return asyncio.run(run())


def test_request_shape_and_text_extraction():
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(200, json=_gemini_response())

    assert _call(handler) == '{"ok": true}'

    request = captured["request"]
    body = json.loads(request.content)
    assert str(request.url) == "https://example.test/v1beta/models/test-model:generateContent"
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in str(request.url)
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    inline = body["contents"][0]["parts"][0]["inlineData"]
    assert inline == {"mimeType": "image/png", "data": base64.b64encode(b"img").decode()}
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseSchema"] == {
        "type": "OBJECT",
        "properties": {"value": {"type": "STRING", "nullable": True}},
    }


def test_thought_parts_are_ignored():
    response = {
        "candidates": [
            {"content": {"parts": [{"text": "thinking", "thought": True}, {"text": "{}"}]}, "finishReason": "STOP"}
        ]
    }
    assert _call(lambda request: httpx.Response(200, json=response)) == "{}"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "boom"}),
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
        httpx.Response(200, json={"candidates": []}),
        httpx.Response(200, json=_gemini_response(finish_reason="MAX_TOKENS")),
        httpx.Response(200, json={"candidates": [{"finishReason": "STOP"}]}),
    ],
)
def test_error_responses_raise(response):
    with pytest.raises(VisionProviderError):
        _call(lambda request: response)


def test_network_errors_raise_without_leaking_key():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(VisionProviderError) as exc_info:
        _call(handler)
    assert "test-key" not in str(exc_info.value)


def test_factory_requires_api_key():
    with pytest.raises(VisionProviderNotConfiguredError):
        get_vision_provider(Settings(_env_file=None, GEMINI_API_KEY=None))


def test_factory_rejects_unknown_provider():
    with pytest.raises(VisionProviderNotConfiguredError):
        get_vision_provider(Settings(_env_file=None, VISION_PROVIDER="other", GEMINI_API_KEY="k"))


def test_factory_builds_gemini_provider():
    provider = get_vision_provider(Settings(_env_file=None, GEMINI_API_KEY="k"))
    assert isinstance(provider, GeminiVisionProvider)
