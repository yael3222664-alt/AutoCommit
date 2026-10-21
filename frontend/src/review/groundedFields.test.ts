import { describe, expect, it } from "vitest";

import { makeRecord } from "../test/fixtures";
import { commitmentLabel, getGroundedFields } from "./groundedFields";

describe("getGroundedFields", () => {
  it("lists text fields then central and commitment items with stable keys and labels", () => {
    const fields = getGroundedFields(makeRecord());
    expect(fields.map((f) => [f.key, f.label])).toEqual([
      ["first_name", "שם פרטי"],
      ["last_name", "שם משפחה"],
      ["id_number", "תעודת זהות"],
      ["received_date", "תאריך קבלה"],
      ["sender", "גורם שולח"],
      ["central_numbers.0", "מספר מרכזי"],
      ["commitment_numbers.0", "טופס 17"],
    ]);
    expect(fields[0]).toMatchObject({ value: "Dana", confidence: "high", textField: "first_name" });
    expect(fields[0]?.box).toEqual({ ymin: 0.1, xmin: 0.2, ymax: 0.3, xmax: 0.4, page_number: 1 });
    expect(fields[5]).toMatchObject({ value: "000123", textField: null, box: null });
  });

  it("drops boxes that belong to a different page than the record", () => {
    const box = { ymin: 0.1, xmin: 0.1, ymax: 0.2, xmax: 0.2, page_number: 1 };
    const record = makeRecord({
      page_number: 2,
      first_name: { value: "Dana", confidence: "high", box, raw_value: null },
      central_numbers: [{ value: "1", confidence: "high", source: null, box: { ...box, page_number: 2 } }],
    });
    const fields = getGroundedFields(record);
    expect(fields.find((f) => f.key === "first_name")?.box).toBeNull();
    expect(fields.find((f) => f.key === "central_numbers.0")?.box).toEqual({ ...box, page_number: 2 });
  });

  it("maps every label_type the extraction prompt produces and falls back otherwise", () => {
    expect(["commitment", "reference", "order", "sample"].map(commitmentLabel)).toEqual(["טופס 17", "אסמכתא", "מספר דרישה", "דגימה"]);
    expect(commitmentLabel(null)).toBe("מספר התחייבות");
    expect(commitmentLabel("unknown")).toBe("מספר התחייבות");
  });
});
