import {
  CONFIDENCE_LEVELS,
  RECORD_STATUSES,
  type BoundingBox,
  type CentralNumberItem,
  type CommitmentNumberItem,
  type ConfidenceLevel,
  type FieldValue,
  type RecordListResponse,
  type RecordResponse,
  type RecordStatus,
} from "../types/record";

/** Thrown when a server response does not match the backend contract. */
export class ResponseShapeError extends Error {
  constructor(public readonly path: string, expected: string) {
    super(`Invalid response at ${path}: expected ${expected}`);
    this.name = "ResponseShapeError";
  }
}

type Json = Record<string, unknown>;

function object(value: unknown, path: string): Json {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ResponseShapeError(path, "object");
  }
  return value as Json;
}

function string(value: unknown, path: string): string {
  if (typeof value !== "string") throw new ResponseShapeError(path, "string");
  return value;
}

function nullableString(value: unknown, path: string): string | null {
  return value === null || value === undefined ? null : string(value, path);
}

function finiteNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new ResponseShapeError(path, "finite number");
  return value;
}

function integer(value: unknown, path: string, min: number): number {
  const number = finiteNumber(value, path);
  if (!Number.isInteger(number) || number < min) throw new ResponseShapeError(path, `integer >= ${min}`);
  return number;
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") throw new ResponseShapeError(path, "boolean");
  return value;
}

function array<T>(value: unknown, path: string, item: (value: unknown, path: string) => T): T[] {
  if (!Array.isArray(value)) throw new ResponseShapeError(path, "array");
  return value.map((entry, index) => item(entry, `${path}[${index}]`));
}

function oneOf<T extends string>(options: readonly T[], value: unknown, path: string): T {
  if (typeof value !== "string" || !(options as readonly string[]).includes(value)) {
    throw new ResponseShapeError(path, options.join(" | "));
  }
  return value as T;
}

function unitInterval(value: unknown, path: string): number {
  const number = finiteNumber(value, path);
  if (number < 0 || number > 1) throw new ResponseShapeError(path, "number in [0, 1]");
  return number;
}

function boundingBox(value: unknown, path: string): BoundingBox | null {
  if (value === null || value === undefined) return null;
  const raw = object(value, path);
  const box = {
    ymin: unitInterval(raw.ymin, `${path}.ymin`),
    xmin: unitInterval(raw.xmin, `${path}.xmin`),
    ymax: unitInterval(raw.ymax, `${path}.ymax`),
    xmax: unitInterval(raw.xmax, `${path}.xmax`),
    page_number: integer(raw.page_number ?? 1, `${path}.page_number`, 1),
  };
  if (box.ymin > box.ymax || box.xmin > box.xmax) throw new ResponseShapeError(path, "ymin <= ymax and xmin <= xmax");
  return box;
}

function confidence(value: unknown, path: string): ConfidenceLevel {
  return oneOf(CONFIDENCE_LEVELS, value, path);
}

function fieldValue(value: unknown, path: string): FieldValue {
  const raw = object(value, path);
  return {
    value: nullableString(raw.value, `${path}.value`),
    confidence: confidence(raw.confidence ?? "low", `${path}.confidence`),
    box: boundingBox(raw.box, `${path}.box`),
    raw_value: nullableString(raw.raw_value, `${path}.raw_value`),
  };
}

function centralNumber(value: unknown, path: string): CentralNumberItem {
  const raw = object(value, path);
  return {
    value: string(raw.value, `${path}.value`),
    confidence: confidence(raw.confidence, `${path}.confidence`),
    source: nullableString(raw.source, `${path}.source`),
    box: boundingBox(raw.box, `${path}.box`),
  };
}

function commitmentNumber(value: unknown, path: string): CommitmentNumberItem {
  const raw = object(value, path);
  return {
    value: string(raw.value, `${path}.value`),
    confidence: confidence(raw.confidence, `${path}.confidence`),
    label_type: nullableString(raw.label_type, `${path}.label_type`),
    box: boundingBox(raw.box, `${path}.box`),
  };
}

export function parseRecordResponse(value: unknown, path = "record"): RecordResponse {
  const raw = object(value, path);
  return {
    id: string(raw.id, `${path}.id`),
    document_id: string(raw.document_id, `${path}.document_id`),
    created_at: string(raw.created_at, `${path}.created_at`),
    updated_at: string(raw.updated_at, `${path}.updated_at`),
    page_number: integer(raw.page_number, `${path}.page_number`, 1),
    image_path: nullableString(raw.image_path, `${path}.image_path`),
    first_name: fieldValue(raw.first_name, `${path}.first_name`),
    last_name: fieldValue(raw.last_name, `${path}.last_name`),
    id_number: fieldValue(raw.id_number, `${path}.id_number`),
    central_numbers: array(raw.central_numbers ?? [], `${path}.central_numbers`, centralNumber),
    received_date: fieldValue(raw.received_date, `${path}.received_date`),
    sender: fieldValue(raw.sender, `${path}.sender`),
    commitment_numbers: array(raw.commitment_numbers ?? [], `${path}.commitment_numbers`, commitmentNumber),
    requires_manual_review: boolean(raw.requires_manual_review, `${path}.requires_manual_review`),
    review_reasons: array(raw.review_reasons ?? [], `${path}.review_reasons`, string),
    status: oneOf<RecordStatus>(RECORD_STATUSES, raw.status, `${path}.status`),
  };
}

export function parseRecordList(value: unknown): RecordResponse[] {
  return array(value, "records", parseRecordResponse);
}

export function parseRecordListResponse(value: unknown): RecordListResponse {
  const raw = object(value, "response");
  return {
    items: array(raw.items, "response.items", parseRecordResponse),
    total: integer(raw.total, "response.total", 0),
    limit: integer(raw.limit, "response.limit", 1),
    offset: integer(raw.offset, "response.offset", 0),
  };
}
