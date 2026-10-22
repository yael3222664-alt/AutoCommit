import type { BoundingBox, ConfidenceLevel, RecordResponse, RecordStatus, RecordUpdatePayload } from "../types/record";

/** Single-value fields the review form can edit; matches the string fields of RecordUpdatePayload. */
export const TEXT_FIELDS = ["first_name", "last_name", "id_number", "received_date", "sender"] as const;
export type TextFieldName = (typeof TEXT_FIELDS)[number] & keyof RecordUpdatePayload;

export const FIELD_LABELS: Readonly<Record<TextFieldName, string>> = {
  first_name: "שם פרטי",
  last_name: "שם משפחה",
  id_number: "תעודת זהות",
  received_date: "תאריך קבלה",
  sender: "גורם שולח",
};

export const CENTRAL_NUMBER_LABEL = "מספר מרכזי";
/** Labels for the label_type values the extraction prompt requests. */
const COMMITMENT_LABELS: Readonly<Record<string, string>> = {
  commitment: "טופס 17",
  reference: "אסמכתא",
  order: "מספר דרישה",
  sample: "דגימה",
};
export const COMMITMENT_NUMBER_LABEL = "מספר התחייבות";

/** A field of a record as shown in the review UI, with its visual grounding on this record's page. */
export interface GroundedField {
  /** Stable within a record: a field name, or "<list>.<index>" for list items. */
  key: string;
  label: string;
  value: string | null;
  confidence: ConfidenceLevel;
  /** Null when the field has no box or the box belongs to a different page. */
  box: BoundingBox | null;
  /** Set for fields edited through the form's text inputs. */
  textField: TextFieldName | null;
  /** True when the extractor kept conflicting readings (candidates). */
  ambiguous: boolean;
}

function onPage(box: BoundingBox | null, pageNumber: number): BoundingBox | null {
  return box && box.page_number === pageNumber ? box : null;
}

export function commitmentLabel(labelType: string | null): string {
  return (labelType && COMMITMENT_LABELS[labelType]) ?? COMMITMENT_NUMBER_LABEL;
}

export function getGroundedFields(record: RecordResponse): GroundedField[] {
  const page = record.page_number;
  const textFields = TEXT_FIELDS.map((name): GroundedField => {
    const field = record[name];
    return {
      key: name,
      label: FIELD_LABELS[name],
      value: field.value,
      confidence: field.confidence,
      box: onPage(field.box, page),
      textField: name,
      ambiguous: (field.candidates?.length ?? 0) > 0,
    };
  });
  const central = record.central_numbers.map((item, index): GroundedField => ({
    key: `central_numbers.${index}`,
    label: CENTRAL_NUMBER_LABEL,
    value: item.value,
    confidence: item.confidence,
    box: onPage(item.box, page),
    textField: null,
    ambiguous: false,
  }));
  const commitments = record.commitment_numbers.map((item, index): GroundedField => ({
    key: `commitment_numbers.${index}`,
    label: commitmentLabel(item.label_type),
    value: item.value,
    confidence: item.confidence,
    box: onPage(item.box, page),
    textField: null,
    ambiguous: false,
  }));
  return [...textFields, ...central, ...commitments];
}

export const CONFIDENCE_LABELS: Readonly<Record<ConfidenceLevel, string>> = {
  high: "ביטחון גבוה",
  medium: "ביטחון בינוני",
  low: "ביטחון נמוך",
};

/** Short badges shown next to fields; high confidence gets no badge. */
export const CONFIDENCE_BADGE_LABELS: Readonly<Record<ConfidenceLevel, string>> = {
  high: "",
  medium: "בינוני",
  low: "נמוך",
};

export const AMBIGUOUS_LABEL = "קריאות מתחרות";

export const STATUS_LABELS: Readonly<Record<RecordStatus, string>> = {
  pending_review: "ממתין לבדיקה",
  approved: "מאושר",
  rejected: "נדחה",
  extraction_failed: "החילוץ נכשל",
};

/** Field-level review reasons are prefixed with the field name (e.g. "id_number_invalid_checksum"). */
export function fieldNeedsAttention(field: GroundedField, reviewReasons: readonly string[]): boolean {
  const name = field.key.split(".")[0] ?? field.key;
  return field.confidence !== "high" || field.value === null || reviewReasons.some((reason) => reason.startsWith(`${name}_`));
}
