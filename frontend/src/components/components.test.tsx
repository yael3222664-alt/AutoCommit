import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { getGroundedFields } from "../review/groundedFields";
import { makeRecord } from "../test/fixtures";
import { LoginScreen } from "./AuthGate";
import { DocumentViewer } from "./DocumentViewer";
import { Footer } from "./Footer";
import { RecordsTable } from "./RecordsTable";
import { ReviewForm } from "./ReviewForm";
import { NO_LOCATION_TEXT, SnippetPreview } from "./SnippetPreview";
import { Toolbar } from "./Toolbar";

const BOX = { ymin: 0.1, xmin: 0.2, ymax: 0.3, xmax: 0.4, page_number: 1 };

const record = makeRecord({
  central_numbers: [{ value: "000123", confidence: "high", source: "sticker", box: { ymin: 0.5, xmin: 0.6, ymax: 0.55, xmax: 0.8, page_number: 1 } }],
});
const fields = getGroundedFields(record);

describe("SnippetPreview", () => {
  it("shows the no-location placeholder when the field has no box", () => {
    const html = renderToStaticMarkup(<SnippetPreview src="/img.png" box={null} label="שם משפחה" />);
    expect(html).toContain(NO_LOCATION_TEXT);
    expect(html).toContain("שם משפחה");
    expect(html).not.toContain("<img");
  });

  it("reports an invalid box instead of cropping it", () => {
    const html = renderToStaticMarkup(<SnippetPreview src="/img.png" box={{ ...BOX, ymin: 0.5, ymax: 0.4 }} />);
    expect(html).toContain("המיקום החזותי אינו תקין");
  });

  it("explains when there is no page image", () => {
    expect(renderToStaticMarkup(<SnippetPreview src={null} box={BOX} />)).toContain("תמונת העמוד אינה זמינה");
  });

  it("shows a loading state until the image loads", () => {
    expect(renderToStaticMarkup(<SnippetPreview src="/img.png" box={BOX} />)).toContain("טוען תצוגה מקדימה");
  });
});

describe("DocumentViewer", () => {
  it("renders one positioned overlay per field with a box on this page", () => {
    const html = renderToStaticMarkup(
      <DocumentViewer src="/img.png" alt="עמוד 1" fields={fields} activeKey={null} onSelect={vi.fn()} />,
    );
    expect(html.match(/class="overlay-box/g)).toHaveLength(2);
    expect(html).toContain('data-field="first_name"');
    expect(html).toContain("top:10%;left:20%;width:20%;height:20%");
    expect(html).toContain('data-field="central_numbers.0"');
    expect(html).toContain("top:50%;left:60%;width:20%;height:5%");
    expect(html).not.toContain("overlay-badge");
  });

  it("marks the active overlay and renders its badge", () => {
    const html = renderToStaticMarkup(
      <DocumentViewer src="/img.png" alt="" fields={fields} activeKey="central_numbers.0" onSelect={vi.fn()} />,
    );
    expect(html).toMatch(/class="overlay-box confidence-high active"[^>]*aria-pressed="true"[^>]*data-field="central_numbers.0"/);
    expect(html).toContain('<span class="overlay-badge above">מספר מרכזי</span>');
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(1);
  });

  it("does not draw boxes that fail validation", () => {
    const bad = fields.map((f) => (f.key === "first_name" ? { ...f, box: { ...BOX, xmax: 1.5 } } : f));
    const html = renderToStaticMarkup(<DocumentViewer src="/img.png" alt="" fields={bad} activeKey="first_name" onSelect={vi.fn()} />);
    expect(html).not.toContain('data-field="first_name"');
  });

  it("shows a placeholder when there is no page image", () => {
    const html = renderToStaticMarkup(<DocumentViewer src={null} alt="" fields={fields} activeKey={null} onSelect={vi.fn()} />);
    expect(html).toContain("תמונת העמוד אינה זמינה");
    expect(html).not.toContain("overlay-box");
  });
});

describe("ReviewForm", () => {
  const props = {
    record,
    fields,
    edits: undefined,
    activeKey: "first_name",
    saving: false,
    error: null,
    onFocusField: vi.fn(),
    onHoverField: vi.fn(),
    registerInput: vi.fn(),
    onEdit: vi.fn(),
    onSave: vi.fn(),
  };

  it("renders an input per field, highlights the active one and hints missing locations", () => {
    const html = renderToStaticMarkup(<ReviewForm {...props} />);
    expect(html.match(/<input/g)).toHaveLength(fields.length);
    expect(html).toContain('class="form-field active"><label for="field-first_name">');
    expect(html).toContain('<label for="field-last_name">שם משפחה<span class="conf-badge conf-low"');
    expect(html).toContain('aria-label="רמת ביטחון: ביטחון נמוך"');
    expect(html).toContain('<span class="no-box-hint"> · אין מיקום חזותי זמין</span>');
    expect(html).not.toContain('<label for="field-first_name">שם פרטי<span'); // high confidence: no badge
  });

  it("makes fields read-only on an approved record", () => {
    const html = renderToStaticMarkup(<ReviewForm {...props} record={{ ...record, status: "approved" }} />);
    expect(html.match(/readonly=""/g)).toHaveLength(fields.length);
  });

  it("shows shared unsaved edits and blocks approval until they are saved", () => {
    const html = renderToStaticMarkup(<ReviewForm {...props} edits={{ last_name: "Levi" }} />);
    expect(html).toContain('id="field-last_name" class="confidence-low" value="Levi"');
    expect(html).toMatch(/<button type="button" class="btn btn-success" disabled="">אישור<\/button>/);
    expect(html).toMatch(/<button type="submit" class="btn btn-primary">שמירת תיקונים<\/button>/);
  });
});

describe("SnippetPreview metadata", () => {
  it("shows the zoom, what the engine read and the confidence level of the active field", () => {
    const html = renderToStaticMarkup(
      <SnippetPreview src="/img.png" box={BOX} label="תעודת זהות" reading="18" confidence="medium" zoom={1.2} />,
    );
    expect(html).toContain("חיתוך ×1.2");
    expect(html).toContain("המנוע קרא: <bdi>18</bdi>");
    expect(html).toContain('class="badge confidence-badge confidence-medium">ביטחון בינוני</span>');
  });

  it("omits the metadata when no field is active", () => {
    expect(renderToStaticMarkup(<SnippetPreview src="/img.png" box={null} />)).not.toContain("המנוע קרא");
  });
});

describe("Toolbar", () => {
  const props = {
    query: "",
    onQueryChange: vi.fn(),
    statusFilter: "review" as const,
    onStatusFilterChange: vi.fn(),
    counts: { all: 5, review: 3, saved: 2 },
    pendingCount: 2,
    saving: false,
    failedSaves: 0,
    onSaveAll: vi.fn(),
    uploading: false,
    uploadError: null,
    onUpload: vi.fn(),
  };

  it("renders the status chips with counts, the pressed filter and the pending save count", () => {
    const html = renderToStaticMarkup(<Toolbar {...props} />);
    expect(html).toContain('aria-pressed="false">הכול<span class="filter-chip-count">5</span>');
    expect(html).toContain('aria-pressed="true">לבדיקה<span class="filter-chip-count">3</span>');
    expect(html).toContain('aria-pressed="false">נשמרו<span class="filter-chip-count">2</span>');
    expect(html).toContain('<button type="button" class="btn btn-primary">שמירה (2)</button>');
  });

  it("shows a busy spinner and disables the file input while uploading", () => {
    const html = renderToStaticMarkup(<Toolbar {...props} uploading={true} />);
    expect(html).toContain('class="upload-status" role="status" aria-live="polite" aria-busy="true"');
    expect(html).toContain('class="upload-spinner" aria-hidden="true"');
    expect(html).toContain("מעלה קובץ…");
    expect(html).toContain('<input type="file" accept="application/pdf,image/png,image/jpeg" disabled=""');
  });

  it("offers Excel, CSV and JSON export buttons", () => {
    const html = renderToStaticMarkup(<Toolbar {...props} />);
    expect(html).toContain("Excel (.xlsx)");
    expect(html).toContain("CSV (.csv)");
    expect(html).toContain("JSON (.json)");
    expect(html).toContain(">ייצוא</summary>");
  });

  it("disables batch save with nothing pending and reports failed saves", () => {
    const html = renderToStaticMarkup(<Toolbar {...props} pendingCount={0} failedSaves={1} />);
    expect(html).toContain('disabled="">שמירה (0)</button>');
    expect(html).toContain("1 שמירות נכשלו");
  });
});

describe("RecordsTable", () => {
  const props = {
    records: [record, makeRecord({ id: "rec-2", page_number: 2, status: "approved", requires_manual_review: false, review_reasons: [] })],
    total: 2,
    loading: false,
    drafts: { "rec-1": { first_name: "Dina" } },
    saves: {},
    activeId: "rec-1",
    activeKey: "id_number",
    onSelect: vi.fn(),
    onFocusField: vi.fn(),
    onHoverField: vi.fn(),
    onEdit: vi.fn(),
  };
  const html = renderToStaticMarkup(<RecordsTable {...props} />);

  it("renders a row per record with its page, status and reported source", () => {
    expect(html.match(/<tr class="record-row/g)).toHaveLength(2);
    expect(html).toContain('aria-pressed="true">עמוד 1 <span class="badge tone-review">ממתין לבדיקה</span>');
    expect(html).toContain('עמוד 2 <span class="badge tone-approved">מאושר</span>');
    expect(html).toContain('class="badge source-tag source-sticker">מדבקה חתוכה</span>');
    expect(html).toContain("2 רשומות");
  });

  it("marks edited, attention and active cells and locks approved rows", () => {
    expect(html).toContain('<div class="cell-field edited"><input id="cell-rec-1-first_name"');
    expect(html).toContain('value="Dina"');
    expect(html).toContain('<div class="cell-field attention"><input id="cell-rec-1-last_name"');
    expect(html).toContain('<div class="cell-field active"><input id="cell-rec-1-id_number"');
    expect(html).toMatch(/id="cell-rec-2-first_name"[^>]*readonly=""/);
  });
});

describe("LoginScreen", () => {
  it("renders a password field without echoing any value", () => {
    const html = renderToStaticMarkup(<LoginScreen submitting={false} error={null} onSubmit={() => {}} />);
    expect(html).toContain('type="password"');
    expect(html).toMatch(/autocomplete="current-password"/i);
    expect(html).not.toContain('role="alert"');
  });

  it("shows the error and disables input while submitting", () => {
    const html = renderToStaticMarkup(<LoginScreen submitting error="הסיסמה שגויה" onSubmit={() => {}} />);
    expect(html).toContain("הסיסמה שגויה");
    expect(html).toContain("מתחבר…");
    expect(html).toMatch(/<input[^>]*disabled/);
  });
});


describe("Footer", () => {
  it("renders the logo, creator links, and mailto contacts", () => {
    const html = renderToStaticMarkup(<Footer />);
    expect(html).toContain('src="/logo.png"');
    expect(html).toContain('href="https://github.com/yael3222664-alt"');
    expect(html).toContain("YAEL COHEN");
    expect(html).toContain('href="https://github.com/9697-mali-lo"');
    expect(html).toContain("MALKA LORENTS");
    expect(html).toContain('href="mailto:yael3222664@gmail.com"');
    expect(html).toContain('href="mailto:6719697@gmail.com"');
  });

  it("is not sticky or fixed", () => {
    const html = renderToStaticMarkup(<Footer />);
    expect(html).toContain('class="app-footer"');
    expect(html).not.toContain("sticky");
    expect(html).not.toContain("fixed");
  });
});
