import { useCallback, useMemo, useRef, useState } from "react";

import { useRecordState, useRecordStore } from "../context/RecordStoreContext";
import { getGroundedFields } from "../review/groundedFields";
import { recordImageUrl } from "../services/api";
import type { RecordResponse, RecordUpdatePayload } from "../types/record";
import { DocumentViewer } from "./DocumentViewer";
import { ReviewForm } from "./ReviewForm";
import { SnippetPreview } from "./SnippetPreview";

/** Page image, magnified snippet and review form, all following the same active field. */
export function RecordReview({ record }: { record: RecordResponse }) {
  const store = useRecordStore();
  const save = useRecordState((state) => state.saves[record.id]);
  const fields = useMemo(() => getGroundedFields(record), [record]);
  const imageUrl = record.image_path ? recordImageUrl(record.id) : null;

  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const activeKey = hoveredKey ?? focusedKey;
  const activeField = fields.find((field) => field.key === activeKey) ?? null;

  const inputs = useRef(new Map<string, HTMLInputElement>());
  const registerInput = useCallback((key: string, element: HTMLInputElement | null) => {
    if (element) inputs.current.set(key, element);
    else inputs.current.delete(key);
  }, []);

  function selectFromDocument(key: string) {
    setFocusedKey(key);
    const input = inputs.current.get(key);
    input?.focus();
    input?.scrollIntoView({ block: "nearest" });
  }

  function onSave(payload: RecordUpdatePayload) {
    void store.updateRecord(record.id, payload);
  }

  return (
    <div className="record-review">
      <DocumentViewer
        src={imageUrl}
        alt={`עמוד ${record.page_number}`}
        fields={fields}
        activeKey={activeKey}
        onSelect={selectFromDocument}
        onHover={setHoveredKey}
      />
      <aside className="review-panel">
        <SnippetPreview
          src={imageUrl}
          box={activeField?.box ?? null}
          label={activeField ? activeField.label : null}
        />
        <ReviewForm
          key={`${record.id}:${record.updated_at}`}
          record={record}
          fields={fields}
          activeKey={activeKey}
          saving={save?.status === "saving"}
          error={save?.status === "error" ? (save.error?.message ?? "השמירה נכשלה") : null}
          onFocusField={setFocusedKey}
          onHoverField={setHoveredKey}
          registerInput={registerInput}
          onSave={onSave}
        />
      </aside>
    </div>
  );
}
