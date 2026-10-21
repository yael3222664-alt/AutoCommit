import { useEffect, type ChangeEvent } from "react";

import { RecordReview } from "./components/RecordReview";
import { useRecordState, useRecordStore } from "./context/RecordStoreContext";

export function App() {
  const store = useRecordStore();
  const list = useRecordState((state) => state.list);
  const upload = useRecordState((state) => state.upload);
  const active = useRecordState((state) => state.active);

  useEffect(() => {
    void store.loadRecords();
  }, [store]);

  async function onFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const records = await store.uploadDocument(file);
    if (records?.[0]) void store.selectRecord(records[0].id);
  }

  return (
    <main className="app">
      <header className="app-header">
        <h1>AutoCommit</h1>
        <input
          type="file"
          accept="application/pdf,image/png,image/jpeg"
          disabled={upload.status === "uploading"}
          onChange={(event) => void onFileSelected(event)}
        />
        {upload.status === "uploading" && <p>מעלה…</p>}
        {upload.error && <p role="alert">{upload.error.message}</p>}
      </header>
      <div className="app-body">
        <nav className="record-list">
          {list.error && <p role="alert">{list.error.message}</p>}
          <p>{list.status === "loading" ? "טוען…" : `${list.total} רשומות`}</p>
          <ul>
            {list.items.map((record) => (
              <li key={record.id}>
                <button
                  type="button"
                  className={record.id === active.id ? "selected" : undefined}
                  onClick={() => void store.selectRecord(record.id)}
                >
                  עמוד {record.page_number} · {record.first_name.value ?? "—"} {record.last_name.value ?? ""}
                  {record.requires_manual_review ? " · לבדיקה" : ""}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <section className="record-detail">
          {active.error && <p role="alert">{active.error.message}</p>}
          {active.record ? <RecordReview key={active.record.id} record={active.record} /> : <p>בחרו רשומה מהרשימה</p>}
        </section>
      </div>
    </main>
  );
}
