import { useState, type FormEvent } from "react";

import { TEXT_FIELDS, type GroundedField, type TextFieldName } from "../review/groundedFields";
import type { EditableRecordStatus, RecordResponse, RecordUpdatePayload } from "../types/record";

export interface ReviewFormProps {
  record: RecordResponse;
  fields: readonly GroundedField[];
  activeKey: string | null;
  saving: boolean;
  error: string | null;
  onFocusField(key: string | null): void;
  onHoverField(key: string | null): void;
  registerInput(key: string, element: HTMLInputElement | null): void;
  onSave(payload: RecordUpdatePayload): void;
}

type Draft = Record<TextFieldName, string>;

function draftFrom(record: RecordResponse): Draft {
  return Object.fromEntries(TEXT_FIELDS.map((name) => [name, record[name].value ?? ""])) as Draft;
}

/** Only changed fields are sent; an emptied field is sent as an explicit null. */
export function buildFieldPatch(record: RecordResponse, draft: Draft): RecordUpdatePayload {
  const payload: RecordUpdatePayload = {};
  for (const name of TEXT_FIELDS) {
    const next = draft[name].trim();
    if (next !== (record[name].value ?? "")) payload[name] = next === "" ? null : next;
  }
  return payload;
}

const STATUS_LABELS: Readonly<Record<RecordResponse["status"], string>> = {
  pending_review: "ממתין לבדיקה",
  approved: "מאושר",
  rejected: "נדחה",
  extraction_failed: "החילוץ נכשל",
};

/**
 * Review form. Remount it (key on id + updated_at) to reset the draft after a save.
 * Focus and hover on any input report the field key so the viewer and snippet can follow.
 */
export function ReviewForm(props: ReviewFormProps) {
  const { record, fields, activeKey, saving, error, onFocusField, onHoverField, registerInput, onSave } = props;
  const [draft, setDraft] = useState<Draft>(() => draftFrom(record));
  const locked = record.status === "approved";
  const patch = buildFieldPatch(record, draft);
  const dirty = Object.keys(patch).length > 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (dirty && !saving) onSave(patch);
  }

  function setStatus(status: EditableRecordStatus) {
    if (!saving) onSave({ status });
  }

  return (
    <form className="review-form" onSubmit={submit}>
      <p className="review-status">
        עמוד {record.page_number} · {STATUS_LABELS[record.status]}
        {record.requires_manual_review && " · דורש בדיקה ידנית"}
      </p>
      {fields.map((field) => {
        const id = `field-${field.key}`;
        const textField = field.textField;
        return (
          <div
            key={field.key}
            className={`form-field${field.key === activeKey ? " active" : ""}`}
            onMouseEnter={() => onHoverField(field.key)}
            onMouseLeave={() => onHoverField(null)}
          >
            <label htmlFor={id}>
              {field.label}
              {!field.box && <span className="no-box-hint"> · אין מיקום חזותי זמין</span>}
            </label>
            <input
              id={id}
              ref={(element) => registerInput(field.key, element)}
              className={`confidence-${field.confidence}`}
              value={textField ? draft[textField] : (field.value ?? "")}
              readOnly={!textField || locked}
              placeholder={textField === "received_date" ? "YYYY-MM-DD" : undefined}
              dir={textField && textField !== "first_name" && textField !== "last_name" && textField !== "sender" ? "ltr" : undefined}
              onFocus={() => onFocusField(field.key)}
              onBlur={() => onFocusField(null)}
              onChange={(event) => {
                if (textField) setDraft((current) => ({ ...current, [textField]: event.target.value }));
              }}
            />
          </div>
        );
      })}
      {record.review_reasons.length > 0 && <p className="review-reasons">סיבות לבדיקה: {record.review_reasons.join(", ")}</p>}
      {error && <p role="alert">{error}</p>}
      <div className="form-actions">
        <button type="submit" disabled={!dirty || saving || locked}>
          {saving ? "שומר…" : "שמירת תיקונים"}
        </button>
        {record.status === "pending_review" && (
          <>
            <button type="button" disabled={saving || dirty} onClick={() => setStatus("approved")}>
              אישור
            </button>
            <button type="button" disabled={saving} onClick={() => setStatus("rejected")}>
              דחייה
            </button>
          </>
        )}
        {record.status !== "pending_review" && (
          <button type="button" disabled={saving} onClick={() => setStatus("pending_review")}>
            החזרה לבדיקה
          </button>
        )}
      </div>
    </form>
  );
}
