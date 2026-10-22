import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { InspectionPanel } from "./components/InspectionPanel";
import { RecordsTable } from "./components/RecordsTable";
import { ReviewForm } from "./components/ReviewForm";
import { Footer } from "./components/Footer";
import { Toolbar } from "./components/Toolbar";
import { useRecordState, useRecordStore } from "./context/RecordStoreContext";
import { buildFieldPatch, createDraftStore, pendingRecords } from "./review/drafts";
import { getGroundedFields, type TextFieldName } from "./review/groundedFields";
import { countByStatusFilter, matchesSearch, matchesStatusFilter, type StatusFilter } from "./review/recordFilters";
import type { RecordUpdatePayload } from "./types/record";

/** The field the user is working on; it only applies while its record is the active one. */
interface FieldSession {
  recordId: string | null;
  focusedKey: string | null;
  hoveredKey: string | null;
}

const NO_SESSION: FieldSession = { recordId: null, focusedKey: null, hoveredKey: null };

export function App() {
  const store = useRecordStore();
  const list = useRecordState((state) => state.list);
  const upload = useRecordState((state) => state.upload);
  const active = useRecordState((state) => state.active);
  const saves = useRecordState((state) => state.saves);

  const [draftStore] = useState(createDraftStore);
  const drafts = useSyncExternalStore(draftStore.subscribe, draftStore.getState);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [session, setSession] = useState<FieldSession>(NO_SESSION);
  const [savingAll, setSavingAll] = useState(false);

  useEffect(() => {
    void store.loadRecords();
  }, [store]);

  const record = active.record;
  const fields = useMemo(() => (record ? getGroundedFields(record) : []), [record]);
  const activeKey = record && session.recordId === record.id ? (session.hoveredKey ?? session.focusedKey) : null;
  const activeField = fields.find((field) => field.key === activeKey) ?? null;

  const searched = useMemo(() => list.items.filter((item) => matchesSearch(item, query)), [list.items, query]);
  const visible = useMemo(() => searched.filter((item) => matchesStatusFilter(item, statusFilter)), [searched, statusFilter]);
  const counts = useMemo(() => countByStatusFilter(searched), [searched]);
  const pending = useMemo(() => pendingRecords(list.items, drafts), [list.items, drafts]);
  const failedSaves = Object.values(saves).filter((save) => save.status === "error").length;

  const inputs = useRef(new Map<string, HTMLInputElement>());
  const registerInput = useCallback((key: string, element: HTMLInputElement | null) => {
    if (element) inputs.current.set(key, element);
    else inputs.current.delete(key);
  }, []);

  const focusField = useCallback((recordId: string, key: string | null) => {
    setSession((s) => (s.recordId === recordId ? { ...s, focusedKey: key } : { recordId, focusedKey: key, hoveredKey: null }));
  }, []);

  const hoverField = useCallback((recordId: string, key: string | null) => {
    setSession((s) => (s.recordId === recordId ? { ...s, hoveredKey: key } : s));
  }, []);

  function focusFromTable(recordId: string, key: string | null) {
    if (key !== null && recordId !== active.id) void store.selectRecord(recordId);
    focusField(recordId, key);
  }

  function selectFromDocument(key: string) {
    if (!record) return;
    focusField(record.id, key);
    const input = inputs.current.get(key);
    input?.focus();
    input?.scrollIntoView({ block: "nearest" });
  }

  async function saveRecord(recordId: string, payload: RecordUpdatePayload) {
    const saved = await store.updateRecord(recordId, payload);
    if (saved) draftStore.prune(saved);
  }

  async function saveAll() {
    if (savingAll) return;
    setSavingAll(true);
    try {
      await Promise.all(pending.map((item) => saveRecord(item.id, buildFieldPatch(item, drafts[item.id]))));
    } finally {
      setSavingAll(false);
    }
  }

  async function onUpload(file: File) {
    const records = await store.uploadDocument(file);
    if (records?.[0]) void store.selectRecord(records[0].id);
  }

  const save = record ? saves[record.id] : undefined;

  return (
    <div className="app">
      <Toolbar
        query={query}
        onQueryChange={setQuery}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        counts={counts}
        pendingCount={pending.length}
        saving={savingAll}
        failedSaves={failedSaves}
        onSaveAll={() => void saveAll()}
        uploading={upload.status === "uploading"}
        uploadError={upload.error?.message ?? null}
        onUpload={(file) => void onUpload(file)}
      />
      <main className={`workspace${record && inspectorOpen ? "" : " inspector-collapsed"}`}>
        <section className="table-pane">
          {list.error && (
            <p role="alert" className="alert">
              {list.error.message}
            </p>
          )}
          {record && !inspectorOpen && (
            <button type="button" className="btn inspector-reopen" onClick={() => setInspectorOpen(true)}>
              הצגת המסמך
            </button>
          )}
          <RecordsTable
            records={visible}
            total={list.total}
            loading={list.status === "loading"}
            drafts={drafts}
            saves={saves}
            activeId={active.id}
            activeKey={activeKey}
            onSelect={(id) => void store.selectRecord(id)}
            onFocusField={focusFromTable}
            onHoverField={hoverField}
            onEdit={(id, field, text) => draftStore.setField(id, field, text)}
          />
          {active.error && (
            <p role="alert" className="alert">
              {active.error.message}
            </p>
          )}
          {record ? (
            <ReviewForm
              key={record.id}
              record={record}
              fields={fields}
              edits={drafts[record.id]}
              activeKey={activeKey}
              saving={save?.status === "saving"}
              error={save?.status === "error" ? (save.error?.message ?? "השמירה נכשלה") : null}
              onFocusField={(key) => focusField(record.id, key)}
              onHoverField={(key) => hoverField(record.id, key)}
              registerInput={registerInput}
              onEdit={(field: TextFieldName, text) => draftStore.setField(record.id, field, text)}
              onSave={(payload) => void saveRecord(record.id, payload)}
            />
          ) : (
            <p className="card empty-state">בחרו רשומה מהטבלה</p>
          )}
        </section>
        {record && inspectorOpen && (
          <InspectionPanel
            record={record}
            fields={fields}
            activeField={activeField}
            onSelectField={selectFromDocument}
            onHoverField={(key) => hoverField(record.id, key)}
            onCollapse={() => setInspectorOpen(false)}
          />
        )}
      </main>
      <Footer />
    </div>
  );
}
