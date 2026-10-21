import { describe, expect, it } from "vitest";

import { makeRecord } from "../test/fixtures";
import { countByStatusFilter, matchesSearch, matchesStatusFilter } from "./recordFilters";
import { recordSourceKinds } from "./sourceTags";

const record = makeRecord({ last_name: { value: "כהן", confidence: "high", box: null, raw_value: null } });

describe("matchesSearch", () => {
  it.each([
    ["", true],
    ["  ", true],
    ["dana", true],
    ["DANA כהן", true],
    ["כהן dana", true],
    ["000000018", true],
    ["0000 00018", true],
    ["123", true],
    ["levi", false],
    ["999", false],
  ])("%j -> %s", (query, expected) => {
    expect(matchesSearch(record, query)).toBe(expected);
  });

  it("ignores missing values", () => {
    const empty = makeRecord({
      first_name: { value: null, confidence: "low", box: null, raw_value: null },
      id_number: { value: null, confidence: "low", box: null, raw_value: null },
      central_numbers: [],
    });
    expect(matchesSearch(empty, "1")).toBe(false);
  });
});

describe("status filters", () => {
  const records = [
    makeRecord({ id: "a", status: "pending_review" }),
    makeRecord({ id: "b", status: "extraction_failed" }),
    makeRecord({ id: "c", status: "approved" }),
    makeRecord({ id: "d", status: "rejected" }),
  ];

  it("splits records into review and saved", () => {
    expect(records.filter((r) => matchesStatusFilter(r, "review")).map((r) => r.id)).toEqual(["a", "b"]);
    expect(records.filter((r) => matchesStatusFilter(r, "saved")).map((r) => r.id)).toEqual(["c"]);
    expect(countByStatusFilter(records)).toEqual({ all: 4, review: 2, saved: 1 });
  });
});

describe("recordSourceKinds", () => {
  it("reports only known sources the extraction returned, in a stable order", () => {
    const withSources = makeRecord({
      central_numbers: [
        { value: "1", confidence: "high", source: "handwritten", box: null },
        { value: "2", confidence: "high", source: "sticker", box: null },
        { value: "3", confidence: "high", source: "unknown", box: null },
        { value: "4", confidence: "high", source: null, box: null },
      ],
    });
    expect(recordSourceKinds(withSources)).toEqual(["handwritten", "sticker"]);
    expect(recordSourceKinds(makeRecord({ central_numbers: [] }))).toEqual([]);
  });
});
