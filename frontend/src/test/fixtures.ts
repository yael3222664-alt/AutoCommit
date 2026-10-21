import type { RecordResponse } from "../types/record";

const emptyField = { value: null, confidence: "low", box: null, raw_value: null } as const;

export function makeRecord(overrides: Partial<RecordResponse> = {}): RecordResponse {
  return {
    id: "rec-1",
    document_id: "doc-1",
    created_at: "2026-01-01T12:00:00+00:00",
    updated_at: "2026-01-01T12:00:00+00:00",
    page_number: 1,
    image_path: null,
    first_name: { value: "Dana", confidence: "high", box: { ymin: 0.1, xmin: 0.2, ymax: 0.3, xmax: 0.4, page_number: 1 }, raw_value: null },
    last_name: { ...emptyField },
    id_number: { value: "000000018", confidence: "high", box: null, raw_value: "18" },
    central_numbers: [{ value: "000123", confidence: "high", source: "sticker", box: null }],
    received_date: { ...emptyField },
    sender: { ...emptyField },
    commitment_numbers: [{ value: "0070", confidence: "medium", label_type: "commitment", box: null }],
    requires_manual_review: true,
    review_reasons: ["last_name_missing"],
    status: "pending_review",
    ...overrides,
  };
}

export interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
