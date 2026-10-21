import type { RecordResponse } from "../types/record";

export const SOURCE_KINDS = ["printed", "handwritten", "sticker", "phone"] as const;
export type SourceKind = (typeof SOURCE_KINDS)[number];

export const SOURCE_LABELS: Readonly<Record<SourceKind, string>> = {
  printed: "מודפס",
  handwritten: "כתב יד",
  sticker: "מדבקה חתוכה",
  phone: "צילום מהטלפון",
};

function isSourceKind(value: string | null): value is SourceKind {
  return (SOURCE_KINDS as readonly (string | null)[]).includes(value);
}

/** Source kinds the extraction actually reported for this page; nothing is inferred. */
export function recordSourceKinds(record: RecordResponse): SourceKind[] {
  const reported = new Set(record.central_numbers.map((item) => item.source).filter(isSourceKind));
  return SOURCE_KINDS.filter((kind) => reported.has(kind));
}
