"""Multimodal vision provider adapters."""

import asyncio
import base64
import threading
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from ..config import Settings


class VisionProviderError(Exception):
    """Raised when the vision provider fails or returns an unusable response."""


class VisionProviderNotConfiguredError(VisionProviderError):
    pass


class VisionProviderQuotaError(VisionProviderError):
    """The API key's quota (rate or daily) is exhausted; another key may still have capacity."""


class VisionProviderTimeoutError(VisionProviderError):
    pass


class VisionProvider(Protocol):
    async def generate_json(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> str:
        """Return the model's raw JSON text for one image."""
        ...


def _to_gemini_schema(schema: Any) -> Any:
    """Convert an OpenAPI-style schema to Gemini's format (upper-case type names)."""
    if isinstance(schema, dict):
        return {
            key: value.upper() if key == "type" else _to_gemini_schema(value)
            for key, value in schema.items()
        }
    if isinstance(schema, list):
        return [_to_gemini_schema(item) for item in schema]
    return schema


# Overload and rate-limit responses are worth retrying; other errors are not.
# 429 is deliberately absent: a quota-exhausted key will not recover during backoff,
# so the request fails fast and RotatingGeminiProvider moves to the next key.
RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})
_MAX_RETRY_DELAY_SECONDS = 30.0


def _api_error_message(response: httpx.Response) -> str:
    """Google's error message (describes the request, never the image content)."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return ""
    message = error.get("message") if isinstance(error, dict) else None
    return f": {message[:300]}" if isinstance(message, str) and message else ""


def _retry_delay(response: httpx.Response, attempt: int, base_delay: float) -> float:
    retry_after = response.headers.get("retry-after", "")
    delay = float(retry_after) if retry_after.isdigit() else base_delay * 2**attempt
    return min(delay, _MAX_RETRY_DELAY_SECONDS)


class GeminiVisionProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
        max_retries: int = 0,
        retry_base_delay_seconds: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay_seconds
        self._sleep = sleep
        self._url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
        self._timeout = timeout_seconds
        self._client = client

    async def generate_json(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                        {"text": user_prompt},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": _to_gemini_schema(response_schema),
            },
        }
        response = await self._post_with_retries(body)
        try:
            data = response.json()
        except ValueError as exc:
            raise VisionProviderError("Gemini returned a non-JSON response body") from exc
        return _extract_text(data)

    async def _post_with_retries(self, body: dict[str, Any]) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            response = await self._post(body)
            if response.status_code == 200:
                return response
            if response.status_code not in RETRYABLE_STATUS_CODES or attempt == self._max_retries:
                break
            await self._sleep(_retry_delay(response, attempt, self._retry_base_delay))
        error_type = VisionProviderQuotaError if response.status_code == 429 else VisionProviderError
        raise error_type(f"Gemini returned HTTP {response.status_code}{_api_error_message(response)}")

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        headers = {"x-goog-api-key": self._api_key}
        try:
            if self._client is not None:
                return await self._client.post(self._url, json=body, headers=headers, timeout=self._timeout)
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise VisionProviderTimeoutError(f"Gemini request timed out: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise VisionProviderError(f"Gemini request failed: {type(exc).__name__}") from exc


def _extract_text(data: Any) -> str:
    if not isinstance(data, dict):
        raise VisionProviderError("Gemini response has an unexpected structure")
    feedback = data.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise VisionProviderError(f"Gemini blocked the request: {feedback['blockReason']}")

    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise VisionProviderError("Gemini returned no candidates")
    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    if finish_reason not in (None, "STOP"):
        raise VisionProviderError(f"Gemini did not finish generating: {finish_reason}")

    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise VisionProviderError("Gemini candidate has no content parts")
    return "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought")
    )


class RotatingGeminiProvider:
    """Fail over between several Gemini API keys when a key's quota is exhausted.

    A 429 marks that key as exhausted and the request continues on the next key in
    round-robin order. When every key is exhausted the set resets — quotas renew daily —
    and the error propagates, so the next request tries the first key again.
    """

    def __init__(self, providers: list[GeminiVisionProvider]) -> None:
        if not providers:
            raise ValueError("RotatingGeminiProvider needs at least one key")
        self._providers = list(providers)
        self._exhausted: set[int] = set()
        self._cursor = 0
        self._lock = threading.Lock()

    async def generate_json(self, **kwargs) -> str:
        for _ in range(len(self._providers)):
            index = self._next_index()
            if index is None:
                break
            try:
                return await self._providers[index].generate_json(**kwargs)
            except VisionProviderQuotaError:
                self._mark_exhausted(index)
        with self._lock:
            self._exhausted.clear()
        raise VisionProviderQuotaError(
            f"All {len(self._providers)} Gemini API keys exhausted their quota"
        )

    def _next_index(self) -> int | None:
        with self._lock:
            if len(self._exhausted) == len(self._providers):
                return None
            while self._cursor in self._exhausted:
                self._cursor = (self._cursor + 1) % len(self._providers)
            return self._cursor

    def _mark_exhausted(self, index: int) -> None:
        with self._lock:
            self._exhausted.add(index)
            self._cursor = (index + 1) % len(self._providers)


def get_vision_provider(settings: Settings) -> VisionProvider:
    if settings.VISION_PROVIDER.lower() != "gemini":
        raise VisionProviderNotConfiguredError(
            f"Unsupported VISION_PROVIDER: {settings.VISION_PROVIDER}"
        )
    api_keys = settings.gemini_api_key_list()
    if not api_keys:
        raise VisionProviderNotConfiguredError("GEMINI_API_KEY is not set")
    providers = [
        GeminiVisionProvider(
            api_key=key,
            model=settings.GEMINI_MODEL,
            base_url=settings.GEMINI_API_BASE_URL,
            timeout_seconds=settings.VISION_TIMEOUT_SECONDS,
            max_retries=settings.VISION_MAX_RETRIES,
        )
        for key in api_keys
    ]
    return providers[0] if len(providers) == 1 else RotatingGeminiProvider(providers)
