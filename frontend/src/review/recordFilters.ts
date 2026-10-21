import type { RecordResponse } from "../types/record";

export const STATUS_FILTERS = ["all", "review", "saved"] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];

export const STATUS_FILTER_LABELS: Readonly<Record<StatusFilter, string>> = {
  all: "הכול",
  review: "לבדיקה",
  saved: "נשמרו",
};

/** "לבדיקה": still awaiting a decision. "נשמרו": approved and locked. */
export function matchesStatusFilter(record: RecordResponse, filter: StatusFilter): boolean {
  if (filter === "review") return record.status === "pending_review" || record.status === "extraction_failed";
  if (filter === "saved") return record.status === "approved";
  return true;
}

function normalize(text: string): string {
  return text.normalize("NFKC").toLocaleLowerCase().replace(/\s+/g, " ").trim();
}

/** Matches a free-text query against the patient's name, ID number and central numbers. */
export function matchesSearch(record: RecordResponse, query: string): boolean {
  const q = normalize(query);
  if (!q) return true;
  const first = record.first_name.value ?? "";
  const last = record.last_name.value ?? "";
  const names = [first, last, `${first} ${last}`, `${last} ${first}`].map(normalize);
  if (names.some((name) => name.includes(q))) return true;

  const digits = q.replace(/[\s-]/g, "");
  if (!/^\d+$/.test(digits)) return false;
  const numbers = [record.id_number.value, ...record.central_numbers.map((item) => item.value)];
  return numbers.some((value) => value !== null && value.includes(digits));
}

export function countByStatusFilter(records: readonly RecordResponse[]): Record<StatusFilter, number> {
  return Object.fromEntries(
    STATUS_FILTERS.map((filter) => [filter, records.filter((record) => matchesStatusFilter(record, filter)).length]),
  ) as Record<StatusFilter, number>;
}
