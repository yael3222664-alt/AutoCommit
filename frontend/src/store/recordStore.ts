import { ApiError, isApiError, type RecordApi } from "../services/api";
import type { RecordQueryParams, RecordResponse, RecordUpdatePayload } from "../types/record";

export type RequestStatus = "idle" | "loading" | "success" | "error";

export interface ListState {
  status: RequestStatus;
  items: RecordResponse[];
  total: number;
  query: RecordQueryParams;
  error: ApiError | null;
}

export interface ActiveRecordState {
  id: string | null;
  status: RequestStatus;
  record: RecordResponse | null;
  error: ApiError | null;
}

export interface SaveState {
  status: "saving" | "error";
  error: ApiError | null;
}

export interface UploadState {
  status: "idle" | "uploading" | "success" | "error";
  records: RecordResponse[];
  error: ApiError | null;
}

export interface RecordStoreState {
  list: ListState;
  active: ActiveRecordState;
  /** Per-record save state; a record with no entry has no pending or failed save. */
  saves: Readonly<Record<string, SaveState>>;
  upload: UploadState;
}

export type Listener = () => void;

export interface RecordStore {
  getState(): RecordStoreState;
  subscribe(listener: Listener): () => void;
  /** Fetch a page of records. A newer call cancels and supersedes any older one. */
  loadRecords(query?: RecordQueryParams): Promise<void>;
  /** Make a record active. Cached data is shown immediately while fresh data loads. */
  selectRecord(id: string): Promise<void>;
  clearSelection(): void;
  /**
   * Save a manual correction. Returns the updated record, or null if a save for the same
   * record is already in flight (the duplicate is not sent) or the save failed.
   */
  updateRecord(id: string, payload: RecordUpdatePayload): Promise<RecordResponse | null>;
  /** Upload a document. Returns null if an upload is already in flight or the upload failed. */
  uploadDocument(file: File): Promise<RecordResponse[] | null>;
  isSaving(id: string): boolean;
  dispose(): void;
}

export const initialRecordStoreState: RecordStoreState = {
  list: { status: "idle", items: [], total: 0, query: {}, error: null },
  active: { id: null, status: "idle", record: null, error: null },
  saves: {},
  upload: { status: "idle", records: [], error: null },
};

function toApiError(error: unknown): ApiError {
  if (isApiError(error)) return error;
  return new ApiError("network", error instanceof Error ? error.message : "Unexpected error", { cause: error });
}

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

/** Keep whichever version of a record was updated most recently. */
function newer(current: RecordResponse | undefined, incoming: RecordResponse): RecordResponse {
  if (current && timestamp(current.updated_at) > timestamp(incoming.updated_at)) return current;
  return incoming;
}

function withoutKey<T>(map: Readonly<Record<string, T>>, key: string): Record<string, T> {
  const copy = { ...map };
  delete copy[key];
  return copy;
}

/**
 * Framework-agnostic record state container.
 *
 * Stale-response protection: list and detail loads each carry a sequence number and an
 * AbortController; a response is applied only if it belongs to the latest request.
 * Records returned by PATCH are remembered so a slower, older fetch can never replace
 * them with an outdated version (compared by updated_at).
 */
export function createRecordStore(client: RecordApi): RecordStore {
  let state = initialRecordStoreState;
  const listeners = new Set<Listener>();
  const known = new Map<string, RecordResponse>();
  let listSeq = 0;
  let activeSeq = 0;
  let listController: AbortController | null = null;
  let activeController: AbortController | null = null;
  let uploadInFlight = false;
  const savesInFlight = new Set<string>();
  let disposed = false;

  function setState(update: (previous: RecordStoreState) => RecordStoreState): void {
    if (disposed) return;
    state = update(state);
    listeners.forEach((listener) => listener());
  }

  function reconcile(record: RecordResponse): RecordResponse {
    const resolved = newer(known.get(record.id), record);
    known.set(resolved.id, resolved);
    return resolved;
  }

  async function loadRecords(query: RecordQueryParams = state.list.query): Promise<void> {
    const seq = ++listSeq;
    listController?.abort();
    const controller = new AbortController();
    listController = controller;
    setState((s) => ({ ...s, list: { ...s.list, status: "loading", query, error: null } }));

    try {
      const response = await client.fetchRecords(query, { signal: controller.signal });
      if (seq !== listSeq) return;
      const items = response.items.map(reconcile);
      setState((s) => ({ ...s, list: { status: "success", items, total: response.total, query, error: null } }));
    } catch (error) {
      if (seq !== listSeq) return;
      setState((s) => ({ ...s, list: { ...s.list, status: "error", error: toApiError(error) } }));
    } finally {
      if (listController === controller) listController = null;
    }
  }

  async function selectRecord(id: string): Promise<void> {
    const seq = ++activeSeq;
    activeController?.abort();
    const controller = new AbortController();
    activeController = controller;
    const cached = known.get(id) ?? null;
    setState((s) => ({ ...s, active: { id, status: "loading", record: cached, error: null } }));

    try {
      const record = reconcile(await client.fetchRecordById(id, { signal: controller.signal }));
      if (seq !== activeSeq) return;
      setState((s) => ({ ...s, active: { id, status: "success", record, error: null } }));
    } catch (error) {
      if (seq !== activeSeq) return;
      setState((s) => ({ ...s, active: { ...s.active, status: "error", error: toApiError(error) } }));
    } finally {
      if (activeController === controller) activeController = null;
    }
  }

  function clearSelection(): void {
    activeSeq++;
    activeController?.abort();
    activeController = null;
    setState((s) => ({ ...s, active: initialRecordStoreState.active }));
  }

  function applyUpdatedRecord(record: RecordResponse): void {
    setState((s) => ({
      ...s,
      list: { ...s.list, items: s.list.items.map((item) => (item.id === record.id ? record : item)) },
      active: s.active.id === record.id ? { ...s.active, record } : s.active,
      upload: { ...s.upload, records: s.upload.records.map((item) => (item.id === record.id ? record : item)) },
      saves: withoutKey(s.saves, record.id),
    }));
  }

  async function updateRecord(id: string, payload: RecordUpdatePayload): Promise<RecordResponse | null> {
    if (savesInFlight.has(id)) return null;
    savesInFlight.add(id);
    setState((s) => ({ ...s, saves: { ...s.saves, [id]: { status: "saving", error: null } } }));

    try {
      const record = await client.patchRecord(id, payload);
      known.set(record.id, record);
      applyUpdatedRecord(record);
      return record;
    } catch (error) {
      setState((s) => ({ ...s, saves: { ...s.saves, [id]: { status: "error", error: toApiError(error) } } }));
      return null;
    } finally {
      savesInFlight.delete(id);
    }
  }

  async function uploadDocument(file: File): Promise<RecordResponse[] | null> {
    if (uploadInFlight) return null;
    uploadInFlight = true;
    setState((s) => ({ ...s, upload: { status: "uploading", records: [], error: null } }));

    try {
      const records = (await client.uploadDocuments(file)).map(reconcile);
      setState((s) => ({ ...s, upload: { status: "success", records, error: null } }));
      void loadRecords();
      return records;
    } catch (error) {
      setState((s) => ({ ...s, upload: { status: "error", records: [], error: toApiError(error) } }));
      return null;
    } finally {
      uploadInFlight = false;
    }
  }

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    loadRecords,
    selectRecord,
    clearSelection,
    updateRecord,
    uploadDocument,
    isSaving: (id) => savesInFlight.has(id),
    dispose() {
      listController?.abort();
      activeController?.abort();
      listeners.clear();
      disposed = true;
    },
  };
}
