import type { ChangeEvent } from "react";

import { STATUS_FILTER_LABELS, STATUS_FILTERS, type StatusFilter } from "../review/recordFilters";

export interface ToolbarProps {
  query: string;
  onQueryChange(query: string): void;
  statusFilter: StatusFilter;
  onStatusFilterChange(filter: StatusFilter): void;
  counts: Readonly<Record<StatusFilter, number>>;
  pendingCount: number;
  saving: boolean;
  failedSaves: number;
  onSaveAll(): void;
  uploading: boolean;
  uploadError: string | null;
  onUpload(file: File): void;
}

/** Search, status filter, batch save and upload for the records table. */
export function Toolbar(props: ToolbarProps) {
  const { query, onQueryChange, statusFilter, onStatusFilterChange, counts, pendingCount, saving, failedSaves } = props;

  function onFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) props.onUpload(file);
  }

  return (
    <header className="toolbar">
      <h1 className="toolbar-brand">AutoCommit</h1>
      <input
        type="search"
        className="toolbar-search"
        value={query}
        placeholder="חיפוש לפי שם, תעודת זהות או מספר מרכזי"
        aria-label="חיפוש רשומות"
        onChange={(event) => onQueryChange(event.target.value)}
      />
      <div className="filter-chips" role="group" aria-label="סינון לפי סטטוס">
        {STATUS_FILTERS.map((filter) => (
          <button
            key={filter}
            type="button"
            className="filter-chip"
            aria-pressed={filter === statusFilter}
            onClick={() => onStatusFilterChange(filter)}
          >
            {STATUS_FILTER_LABELS[filter]}
            <span className="filter-chip-count">{counts[filter]}</span>
          </button>
        ))}
      </div>
      <span className="toolbar-spacer" />
      {props.uploading && <span className="toolbar-status">מעלה…</span>}
      {props.uploadError && (
        <span className="toolbar-status error" role="alert">
          {props.uploadError}
        </span>
      )}
      {failedSaves > 0 && (
        <span className="toolbar-status error" role="status">
          {failedSaves} שמירות נכשלו
        </span>
      )}
      <label className="btn upload-button">
        העלאת מסמך
        <input
          type="file"
          accept="application/pdf,image/png,image/jpeg"
          disabled={props.uploading}
          onChange={onFileSelected}
        />
      </label>
      <button type="button" className="btn btn-primary" disabled={pendingCount === 0 || saving} onClick={props.onSaveAll}>
        {saving ? "שומר…" : `שמירה (${pendingCount})`}
      </button>
    </header>
  );
}
