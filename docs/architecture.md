# AutoCommit — System Architecture & Design

## 1. System Overview
AutoCommit is an intake, extraction, and verification engine for medical commitment forms. It processes multi-page scanned documents, extracts key administrative fields, computes visual grounding coordinates (Bounding Boxes), and supports user-guided verification.

---

## 2. Architectural Diagram

```text
[ Client (UI) ]
       │
       ▼ (Multipart Upload)
[ FastAPI Router (/api/documents) ]
       │
       ▼
[ Pipeline Orchestrator ]
       │
       ├─► [ PDF Service ]
       │     └─ PyMuPDF (Render pages to 300 DPI PNGs)
       │
       ├─► [ Vision Extractor Service ]
       │     └─ Multimodal Vision Engine (Structured JSON + Bounding Boxes)
       │
       ├─► [ Normalizer & Validator ]
       │     ├─ ID Check-Digit Algorithm
       │     ├─ Date Prioritization (Stamp > Handwritten)
       │     └─ Sender Entity Whitelist Matching
       │
       └─► [ Repository / Database Layer ]
             ├─ SQLite (local, tests) or PostgreSQL (Supabase), Alembic migrations
             └─ Page images: local directory or private Supabase Storage bucket
```

### Persistence configuration
- `DATABASE_URL` selects SQLite (default) or PostgreSQL. PostgreSQL connections use psycopg 3,
  TLS (`DB_SSL_MODE`, default `require`), pre-ping, a bounded pool and a connect timeout, and
  disable server-side prepared statements so the Supabase transaction pooler works.
- The schema is versioned with Alembic (`backend/app/db/migrations`). Startup applies pending
  migrations (`DB_AUTO_MIGRATE`); `python -m backend.app.db.migrate` does the same as a separate
  step. A database created before migrations existed is compared with the baseline and stamped,
  keeping its data; one that differs is refused unchanged. On PostgreSQL an advisory lock
  serializes concurrent startups.
- `PAGE_IMAGE_STORAGE` selects `local` (`PAGE_IMAGE_DIR`) or `supabase` (private bucket, accessed
  only server-side with the service-role key). Records store the object key
  `<document_id>/page_<n>.<png|jpg>`, never a URL; the frontend always reads images through
  `GET /api/records/{id}/image`. Upload is upsert on that deterministic key, so a retry rewrites
  only its own page. A page whose image could not be stored keeps `image_path = null`; its
  siblings are unaffected, and re-uploading the document links the missing image. The app
  refuses to start if the bucket is public; an unreachable store makes the image endpoint
  return 503 without failing uploads.
- All variables are listed, without values, in `.env.example`.

---

## 3. Directory Layout & Module Responsibilities

```text
backend/
├── app/
│   ├── main.py              # Application entry point, CORS middleware, lifespan events
│   ├── config.py            # Centralized settings via pydantic-settings
│   ├── constants.py         # Closed reference data (SENDING_ENTITIES whitelist)
│   ├── schemas.py           # Core Pydantic V2 models, BoundingBox, and API contracts
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py        # HTTP route handlers (upload, health, query)
│   ├── db/
│   │   ├── models.py        # SQLAlchemy tables (JSONB on PostgreSQL)
│   │   ├── session.py       # Engine factory for SQLite / PostgreSQL
│   │   ├── migrate.py       # Alembic runner, adoption of pre-migration databases
│   │   └── migrations/      # Alembic environment and revisions
│   └── services/
│       ├── __init__.py
│       ├── ingestion_service.py   # Upload validation (size, content-based file type)
│       ├── pdf_service.py         # Document rendering and page isolation
│       ├── document_pipeline.py   # Per-page orchestration, concurrency limit, failure isolation
│       ├── vision_provider.py     # Vision provider interface and Gemini adapter
│       ├── extractor_service.py   # Extraction prompt, response schema, and output parsing
│       ├── page_image_store.py    # Page image store interface, keys, local directory store
│       ├── supabase_image_store.py # Private Supabase Storage store (server-side only)
│       ├── storage_factory.py     # Builds the configured store
│       └── validator_service.py   # Domain normalization and business rule validation
├── tests/                   # Pytest unit and integration test suites
└── requirements.txt         # Production and development dependencies
.env.example                 # Configuration template (repo root, no values)
alembic.ini                  # Alembic CLI configuration
```

---

## 4. Pipeline Specifications

### 4.1 Page Ingestion & Rendering
- Multi-page documents are split immediately into distinct page units.
- Each page is rendered as an independent image buffer at 300 DPI (`Matrix(300/72, 300/72)`).
- Each page is mapped to a dedicated `ExtractedRecord` entity with its respective `page_number`.

### 4.2 Extraction & Visual Grounding
- The vision model receives the rendered page image alongside explicit extraction rules.
- The output contains extracted text accompanied by normalized bounding boxes:
  - `ymin`, `xmin`, `ymax`, `xmax` (values between 0.0 and 1.0).
- Fields extracted:
  - First Name, Last Name
  - ID Number (Israeli National ID)
  - Central Sticker Numbers (`מרכזי`)
  - Receipt Date (Prioritizing "התקבל" stamp over manual notations)
  - Sending Organization (Restricted to known HMOs/Hospitals)
  - Commitment/Reference Numbers

### 4.3 Validation Layer
- ID Validation: Verifies check-digit compliance; prefixes leading zeros if length < 9.
- Date Verification: Normalizes to `YYYY-MM-DD`. Flags anomalies.
- Confidence Scoring: Determines whether a manual review flag (`requires_manual_review`) is raised based on extraction confidence and validation pass/fail states.

---

## 5. Non-Functional Requirements
- Reliability: Per-page processing ensures partial failures do not corrupt adjacent records.
- Maintainability: Clear interfaces between rendering, extraction, and validation logic.
- Extensibility: New document types or alternative vision engines can be added by implementing standard service interfaces.
