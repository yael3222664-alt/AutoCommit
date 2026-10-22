import { recordImageUrl } from "../services/api";
import type { GroundedField } from "../review/groundedFields";
import type { RecordResponse } from "../types/record";
import { DocumentViewer } from "./DocumentViewer";
import { SnippetPreview } from "./SnippetPreview";

export interface InspectionPanelProps {
  record: RecordResponse;
  fields: readonly GroundedField[];
  activeField: GroundedField | null;
  onSelectField(key: string): void;
  onHoverField(key: string | null): void;
  onCollapse(): void;
}

function readingOf(record: RecordResponse, field: GroundedField): string | null {
  return (field.textField && record[field.textField].raw_value) || field.value;
}

function candidatesOf(record: RecordResponse, field: GroundedField): string[] {
  const fieldValue = field.textField && record[field.textField];
  return fieldValue?.candidates ?? [];
}

/** Page image with grounding overlays, and the magnified crop of the active field. */
export function InspectionPanel({ record, fields, activeField, onSelectField, onHoverField, onCollapse }: InspectionPanelProps) {
  const imageUrl = record.image_path ? recordImageUrl(record.id) : null;
  return (
    <aside className="inspection-panel" aria-label="בדיקת המסמך">
      <section className="card viewer-card">
        <header className="card-header">
          <h2 className="card-title">עמוד {record.page_number}</h2>
          <button type="button" className="icon-button" aria-label="סגירת תצוגת המסמך" onClick={onCollapse}>
            ×
          </button>
        </header>
        <DocumentViewer
          src={imageUrl}
          alt={`עמוד ${record.page_number}`}
          fields={fields}
          activeKey={activeField?.key ?? null}
          onSelect={onSelectField}
          onHover={onHoverField}
        />
      </section>
      <SnippetPreview
        src={imageUrl}
        box={activeField?.box ?? null}
        label={activeField?.label ?? null}
        reading={activeField ? readingOf(record, activeField) : null}
        candidates={activeField ? candidatesOf(record, activeField) : []}
        confidence={activeField?.confidence ?? null}
      />
    </aside>
  );
}
