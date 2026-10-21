import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { getGroundedFields } from "../review/groundedFields";
import { makeRecord } from "../test/fixtures";
import { DocumentViewer } from "./DocumentViewer";
import { buildFieldPatch, ReviewForm } from "./ReviewForm";
import { NO_LOCATION_TEXT, SnippetPreview } from "./SnippetPreview";

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
    activeKey: "first_name",
    saving: false,
    error: null,
    onFocusField: vi.fn(),
    onHoverField: vi.fn(),
    registerInput: vi.fn(),
    onSave: vi.fn(),
  };

  it("renders an input per field, highlights the active one and hints missing locations", () => {
    const html = renderToStaticMarkup(<ReviewForm {...props} />);
    expect(html.match(/<input/g)).toHaveLength(fields.length);
    expect(html).toContain('class="form-field active"><label for="field-first_name">');
    expect(html).toContain('<label for="field-last_name">שם משפחה<span class="no-box-hint"> · אין מיקום חזותי זמין</span>');
    expect(html).not.toContain('<label for="field-first_name">שם פרטי<span');
  });

  it("makes fields read-only on an approved record", () => {
    const html = renderToStaticMarkup(<ReviewForm {...props} record={{ ...record, status: "approved" }} />);
    expect(html.match(/readonly=""/g)).toHaveLength(fields.length);
  });

  it("builds a patch with only changed fields and null for emptied ones", () => {
    const draft = { first_name: "", last_name: " Levi ", id_number: "000000018", received_date: "", sender: "" };
    expect(buildFieldPatch(record, draft)).toEqual({ first_name: null, last_name: "Levi" });
    expect(buildFieldPatch(record, { ...draft, first_name: "Dana", last_name: "" })).toEqual({});
  });
});
