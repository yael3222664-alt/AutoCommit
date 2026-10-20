# AutoCommit Architecture

This document describes the current state of the codebase and how it maps onto the target design defined in [`AGENTS.md`](../AGENTS.md). Update it whenever module responsibilities, dependencies, or pipeline stages change.

## Current status

The repository contains the backend scaffold only: configuration, data schemas, a FastAPI app with a health endpoint, and an empty API router. No processing pipeline stages, persistence, or tests are implemented yet.

## Repository layout

```
backend/
  requirements.txt       Python dependencies
  .env.example           Example environment configuration
  app/
    main.py              FastAPI application entry point
    config.py            Settings (pydantic-settings)
    schemas.py           Pydantic V2 data contracts
    api/
      routes.py          API router (mounted at /api)
    services/            Infrastructure/service layer (empty)
```

Run locally from the repository root:

```bash
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/health
```

## Module responsibilities

| Module | Layer | Responsibility |
| --- | --- | --- |
| `app/main.py` | API/Transport | Creates the `FastAPI` app from settings, configures `CORSMiddleware` with `settings.ALLOWED_ORIGINS`, exposes `GET /api/health`, and mounts `api.routes.router` under `/api`. |
| `app/config.py` | Configuration | `Settings` (`BaseSettings`) with `APP_NAME`, `DEBUG`, `ALLOWED_ORIGINS`. Loads `backend/.env` regardless of working directory. `get_settings()` is cached; a module-level `settings` instance is exported. |
| `app/schemas.py` | Data contracts | Pydantic V2 models: `ConfidenceLevel`, `FieldValue`, `CentralNumberItem`, `CommitmentNumberItem`, `ExtractedRecord`, `RecordResponse`. |
| `app/api/routes.py` | API/Transport | Empty `APIRouter`; home for thin upload/record endpoints. |
| `app/services/` | Infrastructure/Services | Empty package; home for PDF rendering, vision provider, and storage adapters. |

### Data contracts (`app/schemas.py`)

- `ConfidenceLevel`: `high` | `medium` | `low`.
- `FieldValue`: `value: Optional[str] = None`, `confidence` (default `low`). Missing/unreadable fields stay `None`.
- `CentralNumberItem`: `value`, `confidence`, optional `source` (e.g. `sticker`, `handwritten`).
- `CommitmentNumberItem`: `value`, `confidence`, optional `label_type` (e.g. `commitment`, `order`, `sample`).
- `ExtractedRecord`: one commitment record — `first_name`, `last_name`, `id_number`, `central_numbers`, `received_date` (ISO `YYYY-MM-DD` or `None`), `sender`, `commitment_numbers`, `requires_manual_review` (default `False`), `status` (default `pending_review`).
- `RecordResponse`: `ExtractedRecord` plus `id` and `created_at`.

## Dependencies

### Internal

```
main.py ──> config.py
        └─> api/routes.py
schemas.py   (no internal imports)
```

There are no circular imports. `schemas.py` is not yet consumed by any module.

### External (`backend/requirements.txt`)

| Package | Purpose |
| --- | --- |
| `fastapi`, `uvicorn` | HTTP API and ASGI server |
| `pydantic`, `pydantic-settings`, `python-dotenv` | Data models and environment-based configuration |
| `pymupdf` | PDF page rendering (pipeline stage 2; not yet used) |
| `python-multipart` | Multipart file uploads (not yet used) |

## Document processing pipeline

Target pipeline per `AGENTS.md` §3. Each uploaded document is split into pages, and each page is processed as an independent record; a failure on one page must not affect its siblings.

```
Upload ─> 1. Ingestion & Validation
          2. Document Normalization (PyMuPDF, 300 DPI per page)
          ┌──────────── per page ────────────┐
          3. Vision Extraction (JSON schema-enforced)
          4. Parsing & Validation (Pydantic)
          5. Rule-Based Normalization & Grounding
          6. Persistence & Response Construction
          └──────────────────────────────────┘
       ─> N records for an N-page document
```

| # | Stage | Layer | Status |
| --- | --- | --- | --- |
| 1 | Ingestion & Validation — file type/size/integrity, header and MIME checks, safe temp storage | API + Infrastructure | Not implemented |
| 2 | Document Normalization — render PDF pages to 300 DPI images; clean up temp files on success and error | Infrastructure (`services/`) | Not implemented (`pymupdf` installed) |
| 3 | Vision Extraction — call the multimodal provider behind an injectable interface | Infrastructure (`services/`) | Not implemented |
| 4 | Parsing & Validation — map untrusted provider output to Pydantic models | Domain | Models exist in `schemas.py`; parsing not implemented |
| 5 | Rule-Based Normalization & Grounding — Israeli ID check digit and 9-digit padding, `YYYY-MM-DD` dates with arrival stamp ("התקבל") priority, sender matching against a closed authority list, bounding box retention | Domain | Not implemented |
| 6 | Persistence & Response Construction — save records with audit fields, return `RecordResponse` | Orchestration + Infrastructure | `RecordResponse` model exists; persistence not implemented |

## Gaps between current code and `AGENTS.md`

- **Visual grounding:** `FieldValue` and the list item models have no bounding box (`ymin`, `xmin`, `ymax`, `xmax`) or `page_number` fields.
- **Page-level independence:** `ExtractedRecord` has no page reference, and there is no document/page orchestration.
- **Orchestration layer:** no module for application flow or background jobs yet.
- **Dependency injection:** no service interfaces or FastAPI dependency providers yet.
- **Tests:** no test suite exists.
