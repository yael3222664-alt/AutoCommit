# Testing and benchmarking

| Command | What it covers | Needs |
|---|---|---|
| `pytest` (repo root) | Backend unit and API tests | `.venv` with `backend/requirements.txt` |
| `cd frontend && npm test` | Frontend unit tests | `npm install` |
| `cd frontend && npm run test:integration` | Frontend API client and store against the real backend (SQLite, fake vision provider) | `.venv` |
| `cd frontend && npm run test:acceptance` | Real UI in headless Chrome against the Vite dev server and the real backend | `.venv`, Chrome/Chromium |
| `python -m backend.benchmark.run <manifest>` | Real extraction quality against verified ground truth | Gemini API key, documents, ground truth |

## Browser acceptance suite

```bash
cd frontend
npm install
npm run test:acceptance
```

`frontend/acceptance/acceptance.test.ts` starts the backend (`backend.tests.integration_server`) on a
temporary SQLite file, starts Vite pointed at it, and drives the UI with `playwright-core`. It covers
upload of a 3-page PDF, the stored 300 DPI page images, overlay alignment at two viewport sizes,
pixel comparison of every magnified snippet against an independent crop, click-to-focus on boxes,
refused approval of an incomplete record, correction via PATCH, approval and locking, rapid field and
record switching, restart persistence, a deleted page image, image path traversal through the proxy,
records saved before page images existed, and the absence of uncaught errors.

Environment variables:

- `AUTOCOMMIT_PYTHON`: Python with the backend dependencies (default `.venv/bin/python`).
- `CHROME_PATH`: Chrome/Chromium binary (default: first of `google-chrome`, `chromium`, ... on `PATH`,
  then Playwright's `chrome` channel).
- `ACCEPTANCE_CDP_URL`: drive an already running browser over CDP instead of launching one.

## Extraction benchmark

The benchmark runs documents through the same stages as an upload (validation, 300 DPI rendering,
vision extraction, validation and normalization), without the database, and compares each page with
verified ground truth.

Keep documents, manifests and reports out of git: they contain medical data. `backend/data/` is
git-ignored, e.g. `backend/data/benchmark-docs/`.

1. Configure the provider in `.env` or the environment: `GEMINI_API_KEY`, optionally `GEMINI_MODEL`
   (default `gemini-3.5-flash`) and `VISION_MAX_RETRIES` (default 3, for 429/5xx responses).
2. Write a manifest listing the documents, or draft one from the extractor's output:

   ```bash
   python -m backend.benchmark.run backend/data/benchmark-docs/truth.json \
       --draft-ground-truth backend/data/benchmark-docs/draft.json
   ```

   A minimal manifest for drafting is `{"documents": [{"file": "form1.pdf", "id": "form-1", "pages": []}]}`.
   Every drafted page is `"verified": false`. Correct the values and boxes against the page image and
   set `"verified": true`; unverified pages are not scored. Pages whose extraction failed (e.g. the
   model was overloaded after all retries) are left out of the draft and listed, and the command
   exits with status 1; rerun it to fill them in.
3. Run the benchmark:

   ```bash
   python -m backend.benchmark.run backend/data/benchmark-docs/truth.json
   ```

   It prints a Markdown summary and writes `report.md` and `report.json` to
   `backend/data/benchmark/<timestamp>/` (`--out` to change). Values are left out of the reports
   unless `--include-values` is given; documents are named by their manifest `id`.

### Manifest format

```json
{
  "documents": [
    {
      "file": "form1.pdf",
      "id": "form-1",
      "pages": [
        {
          "page_number": 1,
          "first_name": {"value": "דנה", "box": {"ymin": 0.10, "xmin": 0.55, "ymax": 0.14, "xmax": 0.90}},
          "last_name": {"value": null},
          "id_number": {"value": "012345674"},
          "received_date": {"value": "2024-03-05"},
          "sender": {"value": "כללית"},
          "central_numbers": [{"value": "001234"}],
          "commitment_numbers": []
        }
      ]
    }
  ]
}
```

- `file` is relative to the manifest. Only fields present on a page are scored.
- Values use the API's normalized form: ISO dates, 9-digit IDs, canonical sender names.
- `"value": null` (or an empty list) means the field is not on the form; extracting anything is
  counted as hallucinated.
- Boxes are optional and normalized `ymin, xmin, ymax, xmax`, like the API.

### Metrics

- Per field: correct, wrong, missed (on the form, not extracted), hallucinated (not on the form,
  extracted), correctly absent; accuracy and recall. ID and number fields ignore spaces, dashes,
  dots and slashes; text compares after Unicode and whitespace normalization.
- List fields (central and commitment numbers) are matched by value; unmatched extracted items are
  wrong (or hallucinated when none were expected).
- Boxes, where the value was extracted and the truth has a box: IoU, aligned when IoU ≥ 0.5
  (`--iou-threshold`), plus counts of missing boxes and boxes on the wrong page.
- Per page: extraction failures, pages flagged for review, review reasons, seconds per page.
