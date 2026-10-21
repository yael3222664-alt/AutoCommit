"""Runs the real extraction pipeline on documents with verified ground truth and scores it.

    python -m backend.benchmark.run path/to/ground_truth.json [--out DIR] [--include-values]
    python -m backend.benchmark.run path/to/ground_truth.json --draft-ground-truth draft.json

Uses the configured vision provider (VISION_PROVIDER, GEMINI_API_KEY, GEMINI_MODEL from the
environment or .env). Documents are processed exactly as uploads are: file validation, 300 DPI
rendering, extraction, validation and normalization; nothing is written to the database.
Reports go to backend/data/benchmark/<timestamp>/ (git-ignored) by default.

--draft-ground-truth writes the extractor's output in manifest format with every page marked
"verified": false, as a starting point for annotation. Correct it by hand and set
"verified": true; unverified pages are not scored. Pages whose extraction failed are left out of
the draft and listed, and the command exits with status 1 so they can be retried.
"""

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Optional

from ..app.config import get_settings
from ..app.schemas import ExtractedRecord, RecordStatus
from ..app.services.document_pipeline import DocumentPipeline, build_failed_record
from ..app.services.extractor_service import extract_record_from_image
from ..app.services.ingestion_service import validate_upload
from ..app.services.vision_provider import VisionProviderNotConfiguredError, get_vision_provider
from .ground_truth import GroundTruth, TruthDocument, TruthField, TruthItem, TruthPage, TruthBox, load_ground_truth
from .report import build_json_report, render_markdown
from .scoring import DEFAULT_IOU_THRESHOLD, PageResult, score_page, summarize

DEFAULT_OUT_ROOT = Path(__file__).resolve().parents[1] / "data" / "benchmark"

PageExtractor = Callable[[bytes, int], Awaitable[ExtractedRecord]]


@dataclass(frozen=True)
class DocumentRun:
    label: str
    document: TruthDocument
    records: list[ExtractedRecord]
    seconds: dict[int, float]


async def extract_documents(
    truth: GroundTruth, base_dir: Path, extractor: PageExtractor, max_concurrency: int, max_upload_bytes: int
) -> list[DocumentRun]:
    seconds: dict[tuple[str, int], float] = {}

    async def run(label: str, image: bytes, page_number: int) -> ExtractedRecord:
        started = time.perf_counter()
        try:
            return await extractor(image, page_number)
        finally:
            seconds[(label, page_number)] = time.perf_counter() - started

    runs = []
    for index, document in enumerate(truth.documents, start=1):
        label = document.id or f"doc-{index:02d}"
        data = (base_dir / document.file).read_bytes()
        file_type = validate_upload(data, max_upload_bytes)
        pipeline = DocumentPipeline(partial(run, label), max_concurrency=max_concurrency)
        records = await pipeline.process(data, file_type)
        page_seconds = {number: s for (doc, number), s in seconds.items() if doc == label}
        runs.append(DocumentRun(label, document, records, page_seconds))
    return runs


def score_runs(
    runs: list[DocumentRun], threshold: float, include_unverified: bool = False
) -> tuple[list[PageResult], int]:
    results, skipped = [], 0
    for run in runs:
        by_page = {record.page_number: record for record in run.records}
        for truth_page in run.document.pages:
            if not truth_page.verified and not include_unverified:
                skipped += 1
                continue
            record = by_page.get(truth_page.page_number) or build_failed_record(
                truth_page.page_number, "page_not_in_document"
            )
            results.append(
                score_page(run.label, truth_page, record, threshold, run.seconds.get(truth_page.page_number))
            )
    return results, skipped


def _draft_field(field) -> TruthField:
    return TruthField(value=field.value, box=TruthBox.from_box(field.box) if field.value is not None else None)


def draft_ground_truth(runs: list[DocumentRun]) -> GroundTruth:
    documents = []
    for run in runs:
        pages = [
            TruthPage(
                page_number=r.page_number,
                verified=False,
                first_name=_draft_field(r.first_name),
                last_name=_draft_field(r.last_name),
                id_number=_draft_field(r.id_number),
                received_date=_draft_field(r.received_date),
                sender=_draft_field(r.sender),
                central_numbers=[TruthItem(value=i.value, box=TruthBox.from_box(i.box)) for i in r.central_numbers],
                commitment_numbers=[
                    TruthItem(value=i.value, box=TruthBox.from_box(i.box)) for i in r.commitment_numbers
                ],
            )
            for r in run.records
            if r.status is not RecordStatus.extraction_failed
        ]
        documents.append(TruthDocument(file=run.document.file, id=run.document.id, pages=pages))
    return GroundTruth(documents=documents)


def write_reports(out_dir: Path, report: dict, markdown: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(markdown, encoding="utf-8")


def main(argv: Optional[list[str]] = None, extractor: Optional[PageExtractor] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, help="report directory (default: backend/data/benchmark/<timestamp>)")
    parser.add_argument("--iou-threshold", type=float, default=DEFAULT_IOU_THRESHOLD)
    parser.add_argument("--include-values", action="store_true", help="put field values in the report (PHI)")
    parser.add_argument("--include-unverified", action="store_true", help="also score pages marked unverified")
    parser.add_argument("--draft-ground-truth", type=Path, help="write a draft manifest from the extraction")
    args = parser.parse_args(argv)

    settings = get_settings()
    truth = load_ground_truth(args.manifest)
    provider_name, model = settings.VISION_PROVIDER, settings.GEMINI_MODEL
    if extractor is None:
        try:
            extractor = partial(extract_record_from_image, provider=get_vision_provider(settings))
        except VisionProviderNotConfiguredError as exc:
            print(f"Vision provider not configured: {exc}", file=sys.stderr)
            return 2
    else:
        provider_name, model = "injected", "injected"

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    runs = asyncio.run(
        extract_documents(
            truth,
            args.manifest.resolve().parent,
            extractor,
            settings.VISION_MAX_CONCURRENCY,
            settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024,
        )
    )

    if args.draft_ground_truth is not None:
        draft = draft_ground_truth(runs).model_dump_json(indent=2, exclude_none=False)
        args.draft_ground_truth.write_text(draft, encoding="utf-8")
        failed = [
            f"{run.label} page {r.page_number} ({', '.join(r.review_reasons)})"
            for run in runs
            for r in run.records
            if r.status is RecordStatus.extraction_failed
        ]
        drafted = sum(len(run.records) for run in runs) - len(failed)
        print(f"Draft ground truth written to {args.draft_ground_truth}: {drafted} pages, all unverified")
        if failed:
            print(f"Extraction failed, left out of the draft: {'; '.join(failed)}", file=sys.stderr)
            return 1
        return 0

    pages, skipped = score_runs(runs, args.iou_threshold, args.include_unverified)
    summary = summarize(pages, args.iou_threshold)
    metadata = {
        "provider": provider_name,
        "model": model,
        "started_at": started_at,
        "documents": len(runs),
        "skipped_unverified_pages": skipped,
        "values_included": args.include_values,
    }
    out_dir = args.out or DEFAULT_OUT_ROOT / started_at.replace(":", "")
    markdown = render_markdown(summary, pages, metadata, args.include_values)
    write_reports(out_dir, build_json_report(summary, pages, metadata, args.include_values), markdown)
    print(markdown)
    print(f"Reports written to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
