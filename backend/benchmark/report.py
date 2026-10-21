"""Renders benchmark results as JSON and Markdown.

Field values are medical data, so they are left out unless explicitly requested and
documents are referred to by their manifest `id` (or position), never by file name.
"""

from dataclasses import asdict

from .scoring import BoxOutcome, Outcome, PageResult

_OK = (Outcome.correct, Outcome.correct_absent)


def _page_dict(page: PageResult, include_values: bool) -> dict:
    data = asdict(page)
    for result in data["fields"]:
        result["outcome"] = result["outcome"].value
        result["box_outcome"] = result["box_outcome"].value
        result["iou"] = None if result["iou"] is None else round(result["iou"], 3)
        if not include_values:
            del result["expected"], result["extracted"]
    return data


def build_json_report(summary: dict, pages: list[PageResult], metadata: dict, include_values: bool) -> dict:
    return {"metadata": metadata, "summary": summary, "pages": [_page_dict(p, include_values) for p in pages]}


def _pct(value) -> str:
    return "–" if value is None else f"{value * 100:.1f}%"


def _num(value) -> str:
    return "–" if value is None else f"{value:.3f}"


def render_markdown(summary: dict, pages: list[PageResult], metadata: dict, include_values: bool) -> str:
    overall = summary["overall"]
    lines = [
        "# Extraction benchmark",
        "",
        f"- Provider: `{metadata['provider']}`, model: `{metadata['model']}`, run at {metadata['started_at']}",
        f"- Documents: {metadata['documents']}, scored pages: {summary['pages']}"
        f" (skipped unverified: {metadata['skipped_unverified_pages']})",
        f"- Extraction failures: {summary['extraction_failed_pages']} pages;"
        f" flagged for manual review: {summary['pages_flagged_for_review']};"
        f" fully correct: {summary['pages_fully_correct']}",
        f"- Seconds per page: mean {summary['seconds_per_page']['mean']}, max {summary['seconds_per_page']['max']}",
        f"- Overall field accuracy {_pct(overall['accuracy'])}, recall {_pct(overall['recall'])};"
        f" boxes aligned (IoU ≥ {summary['iou_threshold']}) {_pct(overall['boxes']['aligned_rate'])},"
        f" mean IoU {_num(overall['boxes']['mean_iou'])}",
        "",
        "## Values",
        "",
        "| Field | Scored | Accuracy | Recall | Correct | Wrong | Missed | Hallucinated | Correctly absent |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, data in summary["fields"].items():
        o = data["outcomes"]
        lines.append(
            f"| {name} | {data['total']} | {_pct(data['accuracy'])} | {_pct(data['recall'])} | {o['correct']}"
            f" | {o['wrong']} | {o['missed']} | {o['hallucinated']} | {o['correct_absent']} |"
        )
    lines += [
        "",
        "## Bounding boxes",
        "",
        "Scored where the value was extracted and the truth has a box.",
        "",
        "| Field | Scored | Aligned | Mean IoU | Median IoU | Misaligned | No box | Wrong page |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, data in summary["fields"].items():
        b = data["boxes"]
        lines.append(
            f"| {name} | {b['scored']} | {_pct(b['aligned_rate'])} | {_num(b['mean_iou'])} | {_num(b['median_iou'])}"
            f" | {b['misaligned']} | {b['missing']} | {b['wrong_page']} |"
        )
    lines += ["", "## Review reasons raised", ""]
    lines += [f"- `{reason}`: {count}" for reason, count in summary["review_reasons"].items()] or ["- none"]

    issues = [
        (page, result)
        for page in pages
        for result in page.fields
        if result.outcome not in _OK or result.box_outcome in (BoxOutcome.misaligned, BoxOutcome.missing,
                                                               BoxOutcome.wrong_page)
    ]
    lines += ["", "## Errors", ""]
    if not issues:
        lines.append("None.")
    else:
        header = "| Document | Page | Field | Value | Box | IoU |"
        lines += [header + (" Expected | Extracted |" if include_values else ""),
                  "|---|---|---|---|---|---|" + ("---|---|" if include_values else "")]
        for page, r in issues:
            row = (f"| {page.document} | {page.page_number} | {r.field} | {r.outcome.value} | {r.box_outcome.value}"
                   f" | {_num(r.iou)} |")
            if include_values:
                row += f" {r.expected or ''} | {r.extracted or ''} |"
            lines.append(row)
    return "\n".join(lines) + "\n"
