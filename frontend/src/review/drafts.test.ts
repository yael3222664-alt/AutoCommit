import { describe, expect, it, vi } from "vitest";

import { makeRecord } from "../test/fixtures";
import { buildFieldPatch, createDraftStore, fieldText, hasChanges, isFieldEdited, pendingRecords } from "./drafts";

const record = makeRecord();

describe("draft helpers", () => {
  it("sends every touched field — an unchanged value is a confirmation", () => {
    expect(buildFieldPatch(record, { first_name: "", last_name: " Levi ", id_number: "000000018" })).toEqual({
      first_name: null,
      last_name: "Levi",
      id_number: "000000018",
    });
    expect(buildFieldPatch(record, { first_name: " Dana ", last_name: "" })).toEqual({
      first_name: "Dana",
      last_name: null,
    });
    expect(buildFieldPatch(record, undefined)).toEqual({});
  });

  it("shows edited text over the record value", () => {
    expect(fieldText(record, { first_name: "Dina" }, "first_name")).toBe("Dina");
    expect(fieldText(record, undefined, "last_name")).toBe("");
    expect(isFieldEdited(record, { first_name: "Dana" }, "first_name")).toBe(true);
    expect(isFieldEdited(record, undefined, "first_name")).toBe(false);
    expect(hasChanges(record, { sender: "כללית" })).toBe(true);
  });

  it("counts records with real changes as pending, never approved ones", () => {
    const approved = makeRecord({ id: "rec-2", status: "approved" });
    const drafts = { "rec-1": { last_name: "Levi" }, "rec-2": { last_name: "Cohen" }, "rec-3": { last_name: "X" } };
    expect(pendingRecords([record, approved], drafts).map((r) => r.id)).toEqual(["rec-1"]);
    expect(pendingRecords([record], { "rec-1": { first_name: "Dana" } }).map((r) => r.id)).toEqual(["rec-1"]);
    expect(pendingRecords([record], {})).toEqual([]);
  });
});

describe("createDraftStore", () => {
  it("keeps edits per record and notifies subscribers", () => {
    const drafts = createDraftStore();
    const listener = vi.fn();
    drafts.subscribe(listener);
    drafts.setField("rec-1", "last_name", "Levi");
    drafts.setField("rec-2", "sender", "כללית");
    expect(drafts.getState()).toEqual({ "rec-1": { last_name: "Levi" }, "rec-2": { sender: "כללית" } });
    expect(listener).toHaveBeenCalledTimes(2);
    drafts.clear("rec-2");
    expect(drafts.getState()).toEqual({ "rec-1": { last_name: "Levi" } });
  });

  it("prunes only edits the saved record already contains", () => {
    const drafts = createDraftStore();
    drafts.setField("rec-1", "last_name", "Levi");
    drafts.setField("rec-1", "sender", "כללית");
    drafts.prune(makeRecord({ last_name: { value: "Levi", confidence: "high", box: null, raw_value: null } }));
    expect(drafts.getState()).toEqual({ "rec-1": { sender: "כללית" } });
    drafts.prune(makeRecord({ sender: { value: "כללית", confidence: "high", box: null, raw_value: null } }));
    expect(drafts.getState()).toEqual({});
  });
});
