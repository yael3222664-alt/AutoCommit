import json

from backend.app.services.vision_provider import VisionProviderError

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def valid_payload() -> dict:
    def field(value):
        return {"value": value, "confidence": "high", "box": {"ymin": 0.1, "xmin": 0.1, "ymax": 0.2, "xmax": 0.2}}

    return {
        "first_name": field("דנה"),
        "last_name": field("כהן"),
        "id_number": field("123456782"),
        "received_date_stamp": field("05/03/2024"),
        "received_date_handwritten": {"value": None, "confidence": "low", "box": None},
        "sender": field("כללית"),
        "central_numbers": [
            {"value": "123456", "confidence": "high", "source": "sticker", "box": field("123456")["box"]}
        ],
        "commitment_numbers": [],
        "requires_manual_review": False,
    }


class FakeVisionProvider:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = json.dumps(valid_payload()) if response is None else response
        self.error = error
        self.calls: list[dict] = []

    async def generate_json(self, **kwargs) -> str:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def failing_provider() -> FakeVisionProvider:
    return FakeVisionProvider(error=VisionProviderError("Gemini returned HTTP 500"))
