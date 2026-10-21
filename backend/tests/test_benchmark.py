import json

import pytest
from pydantic import ValidationError

from backend.app.services.extractor_service import extract_record_from_image
from backend.app.services.validator_service import finalize_record
from backend.benchmark import run
from backend.benchmark.ground_truth import GroundTruth, TruthBox, TruthPage
from backend.benchmark.scoring import BoxOutcome, Outcome, iou, normalize_value, score_page, summarize
from backend.tests.fakes import FakeVisionProvider, failing_provider
from backend.tests.integration_server import FIELD_BOXES, page_payload, write_sample_pdf


def box(name: str) -> dict:
    ymin, xmin, ymax, xmax = FIELD_BOXES[name]
    return {"ymin": ymin, "xmin": xmin, "ymax": ymax, "xmax": xmax}


def truth_page(**fields) -> TruthPage:
    return TruthPage.model_validate({"page_number": 1, **fields})


def record(**overrides):
    payload = page_payload(1)
    payload.update(overrides)
    return finalize_record(payload, 1)


def outcomes(result, field):
    return [(r.outcome, r.box_outcome) for r in result.fields if r.field == field]


def test_iou():
    a = TruthBox(ymin=0, xmin=0, ymax=0.2, xmax=0.2)
    assert iou(a, a) == pytest.approx(1.0)
    assert iou(a, TruthBox(ymin=0.5, xmin=0.5, ymax=0.6, xmax=0.6)) == 0.0
    assert iou(a, TruthBox(ymin=0, xmin=0.1, ymax=0.2, xmax=0.3)) == pytest.approx(1 / 3)


def test_normalize_value_ignores_separators_only_in_numbers():
    assert normalize_value("id_number", " 012-345 674 ") == "012345674"
    assert normalize_value("central_numbers", "00/12.34") == "001234"
    assert normalize_value("sender", 'רמב״ם  ') == 'רמב"ם'
    assert normalize_value("first_name", "דנה - לוי") == "דנה - לוי"
    assert normalize_value("first_name", "   ") is None


def test_scalar_outcomes_and_box_alignment():
    truth = truth_page(
        first_name={"value": "דנה", "box": box("first_name")},
        last_name={"value": "כהן"},
        id_number={"value": "012345674", "box": box("first_name")},
        received_date={"value": "2024-03-06"},
        sender={"value": None},
    )
    result = score_page("doc", truth, record())
    assert outcomes(result, "first_name") == [(Outcome.correct, BoxOutcome.aligned)]
    assert outcomes(result, "last_name") == [(Outcome.missed, BoxOutcome.not_scored)]
    assert outcomes(result, "id_number") == [(Outcome.correct, BoxOutcome.misaligned)]
    assert outcomes(result, "received_date") == [(Outcome.wrong, BoxOutcome.not_scored)]
    assert outcomes(result, "sender") == [(Outcome.hallucinated, BoxOutcome.not_scored)]
    assert next(r.iou for r in result.fields if r.field == "first_name") == pytest.approx(1.0)


def test_absent_field_extracted_as_absent_and_unscored_fields_skipped():
    result = score_page("doc", truth_page(last_name={"value": None}), record())
    assert [(r.field, r.outcome) for r in result.fields] == [("last_name", Outcome.correct_absent)]


def test_box_missing_and_wrong_page():
    no_box = record(first_name={"value": "דנה", "confidence": "high", "box": None})
    truth = truth_page(first_name={"value": "דנה", "box": box("first_name")})
    assert outcomes(score_page("doc", truth, no_box), "first_name") == [(Outcome.correct, BoxOutcome.missing)]

    elsewhere = record()
    elsewhere.first_name.box.page_number = 2
    assert outcomes(score_page("doc", truth, elsewhere), "first_name") == [(Outcome.correct, BoxOutcome.wrong_page)]


def test_list_fields_match_by_value():
    truth = truth_page(
        central_numbers=[{"value": "001 234", "box": box("central_number")}, {"value": "555"}],
        commitment_numbers=[],
    )
    result = score_page("doc", truth, record())
    assert outcomes(result, "central_numbers") == [
        (Outcome.correct, BoxOutcome.aligned),
        (Outcome.missed, BoxOutcome.not_scored),
    ]
    assert outcomes(result, "commitment_numbers") == [(Outcome.hallucinated, BoxOutcome.not_scored)]

    wrong = score_page("doc", truth_page(commitment_numbers=[{"value": "1"}]), record())
    assert [r.outcome for r in wrong.fields] == [Outcome.missed, Outcome.wrong]

    empty = score_page("doc", truth_page(central_numbers=[]), record(central_numbers=[]))
    assert [r.outcome for r in empty.fields] == [Outcome.correct_absent]


def test_summary_rates():
    truth = truth_page(first_name={"value": "דנה", "box": box("first_name")}, last_name={"value": "כהן"})
    summary = summarize([score_page("doc", truth, record(), seconds=2.0)])
    assert summary["overall"]["accuracy"] == 0.5
    assert summary["fields"]["last_name"]["recall"] == 0.0
    assert summary["fields"]["first_name"]["boxes"]["aligned_rate"] == 1.0
    assert summary["pages_fully_correct"] == 0
    assert summary["review_reasons"] == {"last_name_missing": 1, "flagged_by_model": 1}
    assert summary["seconds_per_page"] == {"mean": 2.0, "max": 2.0}


@pytest.mark.parametrize(
    "page",
    [
        {"page_number": 1, "first_name": {"value": "x", "box": {"ymin": 0.5, "xmin": 0, "ymax": 0.4, "xmax": 1}}},
        {"page_number": 1, "first_name": {"value": "x", "box": {"ymin": 0, "xmin": 0, "ymax": 0, "xmax": 1}}},
        {"page_number": 1, "first_name": {"value": None, "box": {"ymin": 0, "xmin": 0, "ymax": 1, "xmax": 1}}},
        {"page_number": 1, "unknown_field": {"value": "x"}},
    ],
)
def test_manifest_rejects_invalid_pages(page):
    with pytest.raises(ValidationError):
        GroundTruth.model_validate({"documents": [{"file": "a.pdf", "pages": [page]}]})


def test_manifest_rejects_duplicate_pages_and_ids():
    with pytest.raises(ValidationError):
        GroundTruth.model_validate({"documents": [{"file": "a.pdf", "pages": [{"page_number": 1}] * 2}]})
    with pytest.raises(ValidationError):
        GroundTruth.model_validate({"documents": [{"file": f, "id": "x", "pages": []} for f in ("a.pdf", "b.pdf")]})


async def fake_extractor(image: bytes, page_number: int):
    if page_number == 2:
        provider = failing_provider()
    else:
        provider = FakeVisionProvider(response=json.dumps(page_payload(page_number)))
    return await extract_record_from_image(image, page_number, provider=provider)


def page1_truth(**overrides) -> dict:
    page = {
        "page_number": 1,
        "first_name": {"value": "דנה", "box": box("first_name")},
        "last_name": {"value": None},
        "id_number": {"value": "012345674", "box": box("id_number")},
        "received_date": {"value": "2024-03-05", "box": box("received_date_stamp")},
        "sender": {"value": "כללית", "box": box("sender")},
        "central_numbers": [{"value": "001234", "box": box("central_number")}],
        "commitment_numbers": [{"value": "0098761", "box": box("commitment_number")}],
    }
    page.update(overrides)
    return page


def test_run_scores_real_pipeline_and_keeps_values_out_of_reports(tmp_path, capsys):
    write_sample_pdf(tmp_path / "patient-name.pdf")
    manifest = {
        "documents": [
            {
                "file": "patient-name.pdf",
                "pages": [
                    page1_truth(),
                    {"page_number": 2, "first_name": {"value": "רות"}},
                    {"page_number": 3, "verified": False, "first_name": {"value": "יוסי"}},
                    {"page_number": 4, "last_name": {"value": "לוי"}},
                ],
            }
        ]
    }
    (tmp_path / "truth.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    assert run.main([str(tmp_path / "truth.json"), "--out", str(tmp_path / "out")], extractor=fake_extractor) == 0

    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    summary = report["summary"]
    assert report["metadata"]["skipped_unverified_pages"] == 1
    assert summary["pages"] == 3
    assert summary["extraction_failed_pages"] == 2  # page 2 failed, page 4 does not exist
    assert summary["fields"]["first_name"]["outcomes"] == {
        "correct": 1, "wrong": 0, "missed": 1, "hallucinated": 0, "correct_absent": 0,
    }
    assert summary["fields"]["sender"]["boxes"]["aligned_rate"] == 1.0
    assert summary["review_reasons"]["page_not_in_document"] == 1
    assert all(page["seconds"] is not None for page in report["pages"][:2])

    text = (tmp_path / "out" / "report.json").read_text(encoding="utf-8") + (tmp_path / "out" / "report.md").read_text(
        encoding="utf-8"
    )
    for secret in ("דנה", "012345674", "patient-name"):
        assert secret not in text
    assert "doc-01" in capsys.readouterr().out


def test_include_values_puts_values_in_report(tmp_path):
    write_sample_pdf(tmp_path / "a.pdf")
    manifest = {"documents": [{"file": "a.pdf", "id": "form-a", "pages": [page1_truth(last_name={"value": "כהן"})]}]}
    (tmp_path / "truth.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    args = [str(tmp_path / "truth.json"), "--out", str(tmp_path / "out"), "--include-values"]
    assert run.main(args, extractor=fake_extractor) == 0
    markdown = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "| form-a | 1 | last_name | missed | not_scored | – | כהן |  |" in markdown


def test_draft_ground_truth_round_trip(tmp_path, capsys):
    write_sample_pdf(tmp_path / "a.pdf")
    (tmp_path / "truth.json").write_text(json.dumps({"documents": [{"file": "a.pdf", "pages": []}]}))
    draft_path = tmp_path / "draft.json"
    args = [str(tmp_path / "truth.json"), "--draft-ground-truth", str(draft_path)]
    assert run.main(args, extractor=fake_extractor) == 1  # page 2 failed
    assert "doc-01 page 2 (provider_error)" in capsys.readouterr().err

    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    pages = draft["documents"][0]["pages"]
    assert [p["page_number"] for p in pages] == [1, 3]
    assert all(p["verified"] is False for p in pages)
    assert pages[0]["id_number"] == {"value": "012345674", "box": box("id_number")}

    out = tmp_path / "out"
    assert run.main([str(draft_path), "--out", str(out)], extractor=fake_extractor) == 0
    assert json.loads((out / "report.json").read_text())["summary"]["pages"] == 0

    assert run.main([str(draft_path), "--out", str(out), "--include-unverified"], extractor=fake_extractor) == 0
    summary = json.loads((out / "report.json").read_text())["summary"]
    assert summary["overall"]["accuracy"] == 1.0
    assert summary["overall"]["boxes"]["aligned_rate"] == 1.0


def test_run_without_provider_configuration_fails_clearly(tmp_path, monkeypatch, capsys):
    from backend.app.config import get_settings

    (tmp_path / "truth.json").write_text(json.dumps({"documents": [{"file": "a.pdf", "pages": []}]}))
    monkeypatch.setattr(get_settings(), "GEMINI_API_KEY", None)
    assert run.main([str(tmp_path / "truth.json")]) == 2
    assert "not configured" in capsys.readouterr().err
