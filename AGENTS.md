# Engineering Standards & Architecture Requirements


You are working on AutoCommit: a document-processing system that ingests PDF files and scanned images of medical commitment forms containing printed text, stamps, printed stickers, and handwritten annotations.
Your goal is to build a production-oriented, modular, generic, maintainable, testable, and extensible system — not merely to implement features that work for sample files.

---

## 1. Core Operating Principles
- **Page-Level Independence:** Each page of an uploaded multi-page document is an INDEPENDENT, standalone commitment record. A document with N pages must yield N discrete record entities.
- **Visual Grounding:** Every extracted field supports optional normalized bounding box coordinates (`ymin`, `xmin`, `ymax`, `xmax` in the range `0.0` to `1.0`) and a `page_number` to enable snippet cropping in the user interface.
- **Data Integrity:** Never fabricate or invent values. Fields that are unreadable or missing must remain `None`/`null` with a low confidence score and appropriate review flags.

---

## 2. Architecture & Separation of Concerns
- Follow the Single Responsibility Principle strictly.
- Maintain clear boundaries between:
  - **API/Transport:** Thin FastAPI routers.
  - **Orchestration:** Application flow and background job processing.
  - **Domain/Business Logic:** Rule validation, normalization, and scoring.
  - **Infrastructure/Services:** PDF rendering (PyMuPDF), Vision model integration, database/storage.
- Use dependency injection for replaceable services.
- Keep business logic decoupled from HTTP frameworks and external vendor SDKs.
- Avoid circular dependencies and hidden side effects across modules.

---

## 3. Extensible Processing Pipeline
The processing lifecycle must execute in explicit, testable stages:
1. **Ingestion & Validation:** Verify file type, size, and integrity.
2. **Document Normalization:** Render PDF pages into high-resolution images (300 DPI) using PyMuPDF.
3. **Vision Extraction:** Invoke the multimodal vision provider with structured JSON schema enforcement.
4. **Parsing & Validation:** Map raw outputs to strongly typed Pydantic models.
5. **Rule-Based Normalization & Grounding:**
   - Validate Israeli ID numbers (check digit, 9-digit zero-padding).
   - Normalize dates (`YYYY-MM-DD`) with priority: arrival stamp ("התקבל") over handwritten dates.
   - Match sending entities against a closed authority list.
   - Retain bounding box coordinates for each field.
6. **Persistence & Response Construction:** Save records to local storage/database with audit fields.

A failure on one page must not invalidate successfully processed sibling pages.

---

## 4. Schemas and Data Contracts
- Define all entities using Pydantic V2 models.
- Treat external and AI outputs as untrusted data; validate strictly before persistence or returning via API.
- Maintain consistent naming conventions and ISO date formats.

---

## 5. Security & Resource Safety
- Validate file headers and MIME types safely on upload.
- Prevent path traversal and enforce safe temporary file storage.
- Clean up all temporary rendering files on both success and error conditions.
- Keep API keys and credentials in `.env` configurations; never hardcode secrets.
- Redact or omit sensitive identifiers from operational logs.

---

## 6. Testing & Quality Standards
- Provide deterministic unit tests for validation rules, parsing, and error paths.
- Mock all external AI provider calls and filesystem dependencies in unit tests.
- Ensure the test suite runs and passes before committing any code changes.
- Avoid placeholders, dummy implementations, or commented-out logic.

---

## 7. Workflow Before Every Change
1. Inspect the repository, existing modules, and test suites.
2. Identify responsible module boundaries and public contracts.
3. Implement the minimal necessary change to fulfill the requirement.
4. Add or update corresponding unit/integration tests.
5. Run tests locally and verify clean execution before completing the task.
