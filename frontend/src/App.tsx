import { useEffect, type ChangeEvent } from "react";

import { useRecordState, useRecordStore } from "./context/RecordStoreContext";

export function App() {
  const store = useRecordStore();
  const list = useRecordState((state) => state.list);
  const upload = useRecordState((state) => state.upload);

  useEffect(() => {
    void store.loadRecords();
  }, [store]);

  function onFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void store.uploadDocument(file);
  }

  return (
    <main>
      <h1>AutoCommit</h1>
      <input
        type="file"
        accept="application/pdf,image/png,image/jpeg"
        disabled={upload.status === "uploading"}
        onChange={onFileSelected}
      />
      {upload.status === "uploading" && <p>Uploading…</p>}
      {upload.error && <p role="alert">{upload.error.message}</p>}
      {list.error && <p role="alert">{list.error.message}</p>}
      <p>{list.status === "loading" ? "Loading…" : `${list.total} records`}</p>
      <ul>
        {list.items.map((record) => (
          <li key={record.id}>
            {record.page_number} · {record.first_name.value ?? "—"} {record.last_name.value ?? ""} · {record.status}
            {record.requires_manual_review ? " · needs review" : ""}
          </li>
        ))}
      </ul>
    </main>
  );
}
