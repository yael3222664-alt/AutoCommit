import { useMemo } from "react";

import { fieldText, isFieldEdited, type DraftState, type FieldEdits } from "../review/drafts";
import {
  AMBIGUOUS_LABEL,
  CENTRAL_NUMBER_LABEL,
  COMMITMENT_NUMBER_LABEL,
  CONFIDENCE_BADGE_LABELS,
  CONFIDENCE_LABELS,
  FIELD_LABELS,
  STATUS_LABELS,
  fieldNeedsAttention,
  getGroundedFields,
  type GroundedField,
  type TextFieldName,
} from "../review/groundedFields";
import { SOURCE_LABELS, recordSourceKinds } from "../review/sourceTags";
import type { SaveState } from "../store/recordStore";
import type { RecordResponse } from "../types/record";

type Column = { kind: "text"; field: TextFieldName } | { kind: "central" } | { kind: "commitment" };

const COLUMNS: readonly Column[] = [
  { kind: "text", field: "first_name" },
  { kind: "text", field: "last_name" },
  { kind: "text", field: "id_number" },
  { kind: "central" },
  { kind: "commitment" },
  { kind: "text", field: "received_date" },
  { kind: "text", field: "sender" },
];

const LTR_FIELDS: ReadonlySet<TextFieldName> = new Set(["id_number", "received_date"]);

export interface RecordsTableProps {
  records: readonly RecordResponse[];
  total: number;
  loading: boolean;
  drafts: DraftState;
  saves: Readonly<Record<string, SaveState>>;
  activeId: string | null;
  activeKey: string | null;
  onSelect(recordId: string): void;
  onFocusField(recordId: string, key: string | null): void;
  onHoverField(recordId: string, key: string | null): void;
  onEdit(recordId: string, field: TextFieldName, text: string): void;
}

/** One row per page record with inline editing; focusing a cell drives the inspection panel. */
export function RecordsTable(props: RecordsTableProps) {
  const { records, total, loading } = props;
  return (
    <section className="card record-list" aria-label="רשומות">
      <div className="table-scroll">
        <table className="records-table">
          <thead>
            <tr>
              <th scope="col">מקור</th>
              {COLUMNS.map((column) => (
                <th key={column.kind === "text" ? column.field : column.kind} scope="col">
                  {column.kind === "text" ? FIELD_LABELS[column.field] : column.kind === "central" ? CENTRAL_NUMBER_LABEL : COMMITMENT_NUMBER_LABEL}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {records.map((record) => (
              <RecordRow
                key={record.id}
                {...props}
                record={record}
                edits={props.drafts[record.id]}
                save={props.saves[record.id]}
                active={record.id === props.activeId}
              />
            ))}
          </tbody>
        </table>
      </div>
      {records.length === 0 && <p className="empty-state">{loading ? "טוען…" : "אין רשומות להצגה"}</p>}
      <footer className="table-footer">
        <span>{loading ? "טוען…" : `${total} רשומות`}</span>
        {records.length !== total && <span>מוצגות {records.length}</span>}
      </footer>
    </section>
  );
}

interface RecordRowProps extends RecordsTableProps {
  record: RecordResponse;
  edits: FieldEdits | undefined;
  save: SaveState | undefined;
  active: boolean;
}

function RecordRow({ record, edits, save, active, activeKey, onSelect, onFocusField, onHoverField, onEdit }: RecordRowProps) {
  const fields = useMemo(() => new Map(getGroundedFields(record).map((field) => [field.key, field])), [record]);
  const locked = record.status === "approved";
  const statusTone = record.status === "pending_review" ? "review" : record.status === "extraction_failed" ? "failed" : record.status;

  function cell(key: string, field: GroundedField | undefined, text: string, edited: boolean, textField: TextFieldName | null) {
    const attention = !edited && field !== undefined && fieldNeedsAttention(field, record.review_reasons);
    const isActive = active && key === activeKey;
    const classes = ["cell-field", attention && "attention", edited && "edited", isActive && "active"].filter(Boolean).join(" ");
    return (
      <div className={classes} onMouseEnter={() => active && onHoverField(record.id, key)} onMouseLeave={() => active && onHoverField(record.id, null)}>
        <input
          id={`cell-${record.id}-${key}`}
          aria-label={`${field?.label ?? CENTRAL_NUMBER_LABEL} · עמוד ${record.page_number}`}
          value={text}
          readOnly={!textField || locked}
          dir={textField === null || LTR_FIELDS.has(textField) ? "ltr" : undefined}
          placeholder={textField === "received_date" ? "YYYY-MM-DD" : "—"}
          onFocus={() => onFocusField(record.id, key)}
          onBlur={() => onFocusField(record.id, null)}
          onChange={(event) => textField && onEdit(record.id, textField, event.target.value)}
          onKeyDown={(event) => {
            // Enter confirms the current value of a flagged field (kept text counts as a confirmation).
            if (event.key === "Enter" && textField) {
              onEdit(record.id, textField, event.currentTarget.value);
              event.currentTarget.blur();
            }
          }}
        />
        {field && field.confidence !== "high" && !edited && (
          <span
            className={`conf-badge conf-${field.confidence}${field.ambiguous ? " ambiguous" : ""}`}
            title={field.ambiguous ? AMBIGUOUS_LABEL : undefined}
            aria-label={`רמת ביטחון: ${CONFIDENCE_LABELS[field.confidence]}`}
          >
            {field.ambiguous ? "?" : CONFIDENCE_BADGE_LABELS[field.confidence]}
          </span>
        )}
        {(attention || edited) && (
          <span className="cell-badge" aria-hidden="true">
            {edited ? "✓" : "!"}
          </span>
        )}
      </div>
    );
  }

  return (
    <tr className={`record-row${active ? " active" : ""}`}>
      <td className="source-cell">
        <span className={`source-bar status-${record.status}`} aria-hidden="true" />
        <button type="button" className="row-select" aria-pressed={active} onClick={() => onSelect(record.id)}>
          עמוד {record.page_number}{" "}
          <span className={`badge tone-${statusTone}`}>{STATUS_LABELS[record.status]}</span>
        </button>
        <div className="row-tags">
          {recordSourceKinds(record).map((kind) => (
            <span key={kind} className={`badge source-tag source-${kind}`}>
              {SOURCE_LABELS[kind]}
            </span>
          ))}
        </div>
        {save?.status === "error" && <span className="row-error">השמירה נכשלה: {save.error?.message}</span>}
      </td>
      {COLUMNS.map((column) => {
        if (column.kind === "text") {
          const name = column.field;
          return (
            <td key={name}>
              {cell(name, fields.get(name), fieldText(record, edits, name), isFieldEdited(record, edits, name), name)}
            </td>
          );
        }
        const isCommitment = column.kind === "commitment";
        const items = isCommitment ? record.commitment_numbers : record.central_numbers;
        const listKey = isCommitment ? "commitment_numbers" : "central_numbers";
        const first = fields.get(`${listKey}.0`);
        const text = items.map((item) => item.value).join(", ");
        return <td key={listKey}>{cell(first ? first.key : listKey, first, text, false, null)}</td>;
      })}
    </tr>
  );
}
