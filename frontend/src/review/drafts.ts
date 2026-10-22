import type { RecordResponse, RecordUpdatePayload } from "../types/record";
import { TEXT_FIELDS, type TextFieldName } from "./groundedFields";

/** Unsaved text typed into a record's fields; a missing key means "unchanged". */
export type FieldEdits = Partial<Record<TextFieldName, string>>;
export type DraftState = Readonly<Record<string, FieldEdits>>;

export function fieldText(record: RecordResponse, edits: FieldEdits | undefined, name: TextFieldName): string {
  return edits?.[name] ?? record[name].value ?? "";
}

function differs(record: RecordResponse, name: TextFieldName, text: string): boolean {
  return text.trim() !== (record[name].value ?? "");
}

/** Every touched field is sent — an unchanged value is an explicit confirmation (clears its review flag).
 *  An emptied field is sent as an explicit null. */
export function buildFieldPatch(_record: RecordResponse, edits: FieldEdits | undefined): RecordUpdatePayload {
  const payload: RecordUpdatePayload = {};
  for (const name of TEXT_FIELDS) {
    const text = edits?.[name];
    if (text === undefined) continue;
    const next = text.trim();
    payload[name] = next === "" ? null : next;
  }
  return payload;
}

export function hasChanges(record: RecordResponse, edits: FieldEdits | undefined): boolean {
  return Object.keys(buildFieldPatch(record, edits)).length > 0;
}

export function isFieldEdited(_record: RecordResponse, edits: FieldEdits | undefined, name: TextFieldName): boolean {
  return edits?.[name] !== undefined;
}

export function pendingRecords(records: readonly RecordResponse[], drafts: DraftState): RecordResponse[] {
  return records.filter((record) => record.status !== "approved" && hasChanges(record, drafts[record.id]));
}

export interface DraftStore {
  getState(): DraftState;
  subscribe(listener: () => void): () => void;
  setField(recordId: string, name: TextFieldName, text: string): void;
  /** Drop edits that the given (e.g. freshly saved) record already contains. */
  prune(record: RecordResponse): void;
  clear(recordId: string): void;
}

/** Holds unsaved edits for any number of records so they can be saved together. */
export function createDraftStore(): DraftStore {
  let state: DraftState = {};
  const listeners = new Set<() => void>();

  function set(next: DraftState): void {
    state = next;
    listeners.forEach((listener) => listener());
  }

  function withEdits(recordId: string, edits: FieldEdits): DraftState {
    const next = { ...state };
    if (Object.keys(edits).length > 0) next[recordId] = edits;
    else delete next[recordId];
    return next;
  }

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    setField(recordId, name, text) {
      set(withEdits(recordId, { ...state[recordId], [name]: text }));
    },
    prune(record) {
      const edits = state[record.id];
      if (!edits) return;
      const kept = Object.fromEntries(
        Object.entries(edits).filter(([name, text]) => differs(record, name as TextFieldName, text)),
      ) as FieldEdits;
      set(withEdits(record.id, kept));
    },
    clear(recordId) {
      if (state[recordId]) set(withEdits(recordId, {}));
    },
  };
}
