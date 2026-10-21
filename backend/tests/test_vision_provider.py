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
    VisionProviderTimeoutError,
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


def test_timeouts_raise_timeout_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(VisionProviderTimeoutError):
        _call(handler)


def _call_with_retries(responses, max_retries=3):
    requests, delays = [], []

    def handler(request):
        requests.append(request)
        return responses[len(requests) - 1]

    async def sleep(seconds):
        delays.append(seconds)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = GeminiVisionProvider(
                api_key="test-key", model="m", base_url="https://example.test/v1beta", timeout_seconds=5,
                client=client, max_retries=max_retries, retry_base_delay_seconds=1.0, sleep=sleep,
            )
            return await provider.generate_json(
                image_bytes=b"img", mime_type="image/png", system_prompt="s", user_prompt="u", response_schema=SCHEMA
            )

    return run, requests, delays


def test_overload_and_rate_limit_responses_are_retried_with_backoff():
    responses = [
        httpx.Response(503, json={"error": {"message": "high demand"}}),
        httpx.Response(429, headers={"retry-after": "7"}, json={}),
        httpx.Response(200, json=_gemini_response()),
    ]
    run, requests, delays = _call_with_retries(responses)
    assert asyncio.run(run()) == '{"ok": true}'
    assert len(requests) == 3
    assert delays == [1.0, 7.0]


def test_retries_stop_after_the_limit_and_report_the_api_message():
    responses = [httpx.Response(503, json={"error": {"message": "high demand"}})] * 3
    run, requests, delays = _call_with_retries(responses, max_retries=2)
    with pytest.raises(VisionProviderError, match="HTTP 503: high demand"):
        asyncio.run(run())
    assert len(requests) == 3
    assert delays == [1.0, 2.0]


def test_client_errors_are_not_retried():
    responses = [httpx.Response(404, json={"error": {"message": "model not available"}})]
    run, requests, delays = _call_with_retries(responses)
    with pytest.raises(VisionProviderError, match="HTTP 404: model not available"):
        asyncio.run(run())
    assert len(requests) == 1 and delays == []


def test_retry_delay_is_capped():
    responses = [httpx.Response(429, headers={"retry-after": "3600"}, json={}), httpx.Response(200, json=_gemini_response())]
    run, _, delays = _call_with_retries(responses)
    asyncio.run(run())
    assert delays == [30.0]


def test_factory_passes_retry_setting():
    provider = get_vision_provider(Settings(_env_file=None, GEMINI_API_KEY="k", VISION_MAX_RETRIES=5))
    assert provider._max_retries == 5
