import type { FormEvent } from "react";

import { buildFieldPatch, fieldText, type FieldEdits } from "../review/drafts";
import {
  AMBIGUOUS_LABEL,
  CONFIDENCE_BADGE_LABELS,
  CONFIDENCE_LABELS,
  STATUS_LABELS,
  type GroundedField,
  type TextFieldName,
} from "../review/groundedFields";
import type { EditableRecordStatus, RecordResponse, RecordUpdatePayload } from "../types/record";

export interface ReviewFormProps {
  record: RecordResponse;
  fields: readonly GroundedField[];
  /** Unsaved edits for this record, shared with the records table. */
  edits: FieldEdits | undefined;
  activeKey: string | null;
  saving: boolean;
  error: string | null;
  onFocusField(key: string | null): void;
  onHoverField(key: string | null): void;
  registerInput(key: string, element: HTMLInputElement | null): void;
  onEdit(field: TextFieldName, text: string): void;
  onSave(payload: RecordUpdatePayload): void;
}

const LTR_FIELDS: ReadonlySet<TextFieldName> = new Set(["id_number", "received_date"]);

/** Detail form of the active record. Focus and hover report the field key so the viewer and snippet follow. */
export function ReviewForm(props: ReviewFormProps) {
  const { record, fields, edits, activeKey, saving, error, onFocusField, onHoverField, registerInput, onEdit, onSave } = props;
  const locked = record.status === "approved";
  const patch = buildFieldPatch(record, edits);
  const dirty = Object.keys(patch).length > 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (dirty && !saving) onSave(patch);
  }

  function setStatus(status: EditableRecordStatus) {
    if (!saving) onSave({ status });
  }

  return (
    <form className="card review-form" onSubmit={submit}>
      <p className="review-status">
        עמוד {record.page_number} · {STATUS_LABELS[record.status]}
        {record.requires_manual_review && " · דורש בדיקה ידנית"}
      </p>
      <div className="review-fields">
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
                {field.confidence !== "high" && (
                  <span
                    className={`conf-badge conf-${field.confidence}${field.ambiguous ? " ambiguous" : ""}`}
                    title={field.ambiguous ? AMBIGUOUS_LABEL : undefined}
                    aria-label={`רמת ביטחון: ${CONFIDENCE_LABELS[field.confidence]}`}
                  >
                    {field.ambiguous ? `${CONFIDENCE_BADGE_LABELS[field.confidence]} · ${AMBIGUOUS_LABEL}` : CONFIDENCE_BADGE_LABELS[field.confidence]}
                  </span>
                )}
                {!field.box && <span className="no-box-hint"> · אין מיקום חזותי זמין</span>}
              </label>
              <input
                id={id}
                ref={(element) => registerInput(field.key, element)}
                className={`confidence-${field.confidence}`}
                value={textField ? fieldText(record, edits, textField) : (field.value ?? "")}
                readOnly={!textField || locked}
                placeholder={textField === "received_date" ? "YYYY-MM-DD" : undefined}
                dir={!textField || LTR_FIELDS.has(textField) ? "ltr" : undefined}
                onFocus={() => onFocusField(field.key)}
                onBlur={() => onFocusField(null)}
                onChange={(event) => {
                  if (textField) onEdit(textField, event.target.value);
                }}
              />
            </div>
          );
        })}
      </div>
      {record.review_reasons.length > 0 && <p className="review-reasons">סיבות לבדיקה: {record.review_reasons.join(", ")}</p>}
      {error && <p role="alert" className="alert">{error}</p>}
      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={!dirty || saving || locked}>
          {saving ? "שומר…" : "שמירת תיקונים"}
        </button>
        {record.status === "pending_review" && (
          <>
            <button type="button" className="btn btn-success" disabled={saving || dirty} onClick={() => setStatus("approved")}>
              אישור
            </button>
            <button type="button" className="btn btn-danger" disabled={saving} onClick={() => setStatus("rejected")}>
              דחייה
            </button>
          </>
        )}
        {record.status !== "pending_review" && (
          <button type="button" className="btn" disabled={saving} onClick={() => setStatus("pending_review")}>
            החזרה לבדיקה
          </button>
        )}
      </div>
    </form>
  );
}
