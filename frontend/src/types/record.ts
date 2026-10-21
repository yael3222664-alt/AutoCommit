/**
 * Mirrors backend/app/schemas.py. Keep in sync with the Pydantic models.
 *
 * Bounding boxes use the backend convention: ymin, xmin, ymax, xmax normalized to
 * [0.0, 1.0] relative to the page image (origin top-left), plus a 1-based page_number.
 */

export const CONFIDENCE_LEVELS = ["high", "medium", "low"] as const;
/** high: clear and unambiguous; medium: readable with some uncertainty; low: missing, unreadable, ambiguous, or invalid. */
export type ConfidenceLevel = (typeof CONFIDENCE_LEVELS)[number];

export const RECORD_STATUSES = ["pending_review", "approved", "rejected", "extraction_failed"] as const;
export type RecordStatus = (typeof RECORD_STATUSES)[number];

/** Statuses a client may request via PATCH (extraction_failed is set only by the backend). */
export const EDITABLE_RECORD_STATUSES = ["pending_review", "approved", "rejected"] as const;
export type EditableRecordStatus = (typeof EDITABLE_RECORD_STATUSES)[number];

export interface BoundingBox {
  ymin: number;
  xmin: number;
  ymax: number;
  xmax: number;
  page_number: number;
}

export interface FieldValue {
  value: string | null;
  confidence: ConfidenceLevel;
  box: BoundingBox | null;
  /** Text as read before normalization, kept for auditing. */
  raw_value: string | null;
}

export interface CentralNumberItem {
  value: string;
  confidence: ConfidenceLevel;
  source: string | null;
  box: BoundingBox | null;
}

export interface CommitmentNumberItem {
  value: string;
  confidence: ConfidenceLevel;
  label_type: string | null;
  box: BoundingBox | null;
}

export interface ExtractedRecord {
  page_number: number;
  image_path: string | null;
  first_name: FieldValue;
  last_name: FieldValue;
  id_number: FieldValue;
  central_numbers: CentralNumberItem[];
  /** ISO YYYY-MM-DD or null. */
  received_date: FieldValue;
  sender: FieldValue;
  commitment_numbers: CommitmentNumberItem[];
  requires_manual_review: boolean;
  review_reasons: string[];
  status: RecordStatus;
}

export interface RecordResponse extends ExtractedRecord {
  id: string;
  document_id: string;
  /** ISO 8601 UTC timestamps. */
  created_at: string;
  updated_at: string;
}

export interface RecordListResponse {
  items: RecordResponse[];
  total: number;
  limit: number;
  offset: number;
}

export interface CentralNumberUpdate {
  value: string;
  source?: string | null;
}

export interface CommitmentNumberUpdate {
  value: string;
  label_type?: string | null;
}

/**
 * Mirrors RecordUpdateSchema. With exactOptionalPropertyTypes an omitted key means
 * "leave unchanged" and an explicit null means "clear"; `undefined` is not assignable.
 */
export interface RecordUpdatePayload {
  first_name?: string | null;
  last_name?: string | null;
  id_number?: string | null;
  received_date?: string | null;
  sender?: string | null;
  central_numbers?: CentralNumberUpdate[] | null;
  commitment_numbers?: CommitmentNumberUpdate[] | null;
  status?: EditableRecordStatus;
}

/** Query parameters of GET /api/records. */
export interface RecordQueryParams {
  status?: RecordStatus;
  sender?: string;
  requires_manual_review?: boolean;
  /** ISO 8601 datetimes; naive values are interpreted as UTC by the backend. */
  created_from?: string;
  created_to?: string;
  /** 1..200, backend default 50. */
  limit?: number;
  offset?: number;
}
