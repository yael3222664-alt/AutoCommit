"""Runs the real API for the frontend integration suite (frontend/integration/).

Everything is real (upload validation, PDF rendering, extraction parsing, validation,
persistence) except the vision provider, which returns a deterministic payload per page
so no API key or network access is needed. Pages listed in --fail-pages raise a provider
error to exercise page-level failure isolation.

    python -m backend.tests.integration_server --port 8765 --database /tmp/e2e.db --fail-pages 2
    python -m backend.tests.integration_server --write-sample-pdf /tmp/sample.pdf
"""

import argparse
import json
import os
from pathlib import Path

SAMPLE_PAGE_COUNT = 3

FIELD_BOXES = {
    "first_name": (0.10, 0.55, 0.14, 0.90),
    "last_name": (0.16, 0.55, 0.20, 0.90),
    "id_number": (0.22, 0.55, 0.26, 0.90),
    "received_date_stamp": (0.05, 0.05, 0.12, 0.30),
    "sender": (0.02, 0.60, 0.06, 0.95),
    "central_number": (0.30, 0.05, 0.36, 0.25),
    "commitment_number": (0.40, 0.55, 0.44, 0.90),
}

PAGES = {
    1: {"first_name": "דנה", "last_name": None, "id_number": "012345674", "date": "05/03/2024", "sender": "כללית"},
    2: {"first_name": "רות", "last_name": "מזרחי", "id_number": "000000018", "date": "12.04.2024", "sender": "מאוחדת"},
    3: {"first_name": "יוסי", "last_name": "לוי", "id_number": "123456782", "date": "2024-06-30", "sender": "מכבי"},
}


def _box(name: str) -> dict:
    ymin, xmin, ymax, xmax = FIELD_BOXES[name]
    return {"ymin": ymin, "xmin": xmin, "ymax": ymax, "xmax": xmax}


def _field(name: str, value):
    if value is None:
        return {"value": None, "confidence": "low", "box": None}
    return {"value": value, "confidence": "high", "box": _box(name)}


def page_payload(page_number: int) -> dict:
    page = PAGES[page_number]
    return {
        "first_name": _field("first_name", page["first_name"]),
        "last_name": _field("last_name", page["last_name"]),
        "id_number": _field("id_number", page["id_number"]),
        "received_date_stamp": _field("received_date_stamp", page["date"]),
        "received_date_handwritten": _field("received_date_stamp", None),
        "sender": _field("sender", page["sender"]),
        "central_numbers": [
            {"value": f"00{page_number}234", "confidence": "high", "source": "sticker", "box": _box("central_number")}
        ],
        "commitment_numbers": [
            {"value": f"009876{page_number}", "confidence": "high", "label_type": "commitment",
             "box": _box("commitment_number")}
        ],
        "requires_manual_review": page["last_name"] is None,
    }


def write_sample_pdf(path: Path) -> None:
    import pymupdf

    with pymupdf.open() as document:
        for number in range(1, SAMPLE_PAGE_COUNT + 1):
            document.new_page(width=300, height=400).insert_text((30, 40), f"Commitment form page {number}")
        document.save(path)


def serve(port: int, database: Path, fail_pages: set[int]) -> None:
    # Settings and the engine are cached on first import, so configure the database first.
    os.environ["DATABASE_URL"] = f"sqlite:///{database}"

    import uvicorn

    from backend.app.api import routes
    from backend.app.main import app
    from backend.app.services.document_pipeline import DocumentPipeline
    from backend.app.services.extractor_service import extract_record_from_image
    from backend.tests.fakes import FakeVisionProvider, failing_provider

    async def extractor(image_bytes: bytes, page_number: int):
        if page_number in fail_pages:
            provider = failing_provider()
        else:
            provider = FakeVisionProvider(response=json.dumps(page_payload(page_number)))
        return await extract_record_from_image(image_bytes, page_number, provider=provider)

    pipeline = DocumentPipeline(extractor, max_concurrency=3)
    app.dependency_overrides[routes.get_document_pipeline] = lambda: pipeline
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--fail-pages", type=int, nargs="*", default=[])
    parser.add_argument("--write-sample-pdf", type=Path)
    args = parser.parse_args()

    if args.write_sample_pdf is not None:
        write_sample_pdf(args.write_sample_pdf)
        return
    if args.port is None or args.database is None:
        parser.error("--port and --database are required to serve")
    serve(args.port, args.database, set(args.fail_pages))


if __name__ == "__main__":
    main()
