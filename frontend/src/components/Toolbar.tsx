import { useState, type ChangeEvent } from "react";

import { STATUS_FILTER_LABELS, STATUS_FILTERS, type StatusFilter } from "../review/recordFilters";

const EXPORT_FORMATS = ["xlsx", "csv", "json"] as const;
type ExportFormat = (typeof EXPORT_FORMATS)[number];
const EXPORT_LABELS: Record<ExportFormat, string> = {
  xlsx: "Excel (.xlsx)",
  csv: "CSV (.csv)",
  json: "JSON (.json)",
};

/** Fetch the export and trigger exactly one browser download for it. */
async function downloadExport(format: ExportFormat): Promise<void> {
  const response = await fetch(`api/records/export?format=${format}`);
  if (!response.ok) throw new Error(`Export failed with status ${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `autocommit-export.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

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
  const [exporting, setExporting] = useState(false);
  async function onExport(format: ExportFormat) {
    setExporting(true);
    try {
      await downloadExport(format);
    } finally {
      setExporting(false);
    }
  }

  const { query, onQueryChange, statusFilter, onStatusFilterChange, counts, pendingCount, saving, failedSaves } = props;

  function onFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) props.onUpload(file);
  }

  return (
    <header className="toolbar">
      <h1 className="toolbar-brand">
        <img className="brand-logo" src="/logo.png" alt="YMCL" />
      </h1>
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
      {props.uploading && (
        <span className="upload-status" role="status" aria-live="polite" aria-busy="true">
          <span className="upload-spinner" aria-hidden="true" />
          <span className="upload-status-text">מעלה קובץ…</span>
        </span>
      )}
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
      <details className="export-menu">
        <summary className="btn">ייצוא</summary>
        <div className="export-options" role="menu">
          {EXPORT_FORMATS.map((format) => (
            <button
              key={format}
              type="button"
              role="menuitem"
              disabled={exporting}
              onClick={(event) => {
                event.currentTarget.closest("details")?.removeAttribute("open");
                void onExport(format);
              }}
            >
              {EXPORT_LABELS[format]}
            </button>
          ))}
        </div>
      </details>
      <button type="button" className="btn btn-primary" disabled={pendingCount === 0 || saving} onClick={props.onSaveAll}>
        {saving ? "שומר…" : `שמירה (${pendingCount})`}
      </button>
    </header>
  );
}
