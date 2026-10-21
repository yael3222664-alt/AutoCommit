import { describe, expect, it, vi } from "vitest";

import { ApiError, type RecordApi } from "../services/api";
import { deferred, makeRecord } from "../test/fixtures";
import type { RecordListResponse, RecordResponse } from "../types/record";
import { createRecordStore } from "./recordStore";

function listOf(...items: RecordResponse[]): RecordListResponse {
  return { items, total: items.length, limit: 50, offset: 0 };
}

function mockApi(overrides: Partial<RecordApi> = {}): RecordApi {
  return {
    uploadDocuments: vi.fn(() => Promise.resolve([])),
    fetchRecords: vi.fn(() => Promise.resolve(listOf())),
    fetchRecordById: vi.fn((id: string) => Promise.resolve(makeRecord({ id }))),
    patchRecord: vi.fn((id: string) => Promise.resolve(makeRecord({ id }))),
    ...overrides,
  };
}

describe("loadRecords", () => {
  it("moves from idle to loading to success", async () => {
    const response = deferred<RecordListResponse>();
    const store = createRecordStore(mockApi({ fetchRecords: () => response.promise }));
    expect(store.getState().list.status).toBe("idle");

    const loading = store.loadRecords({ status: "pending_review" });
    expect(store.getState().list).toMatchObject({ status: "loading", query: { status: "pending_review" } });

    response.resolve({ ...listOf(makeRecord()), total: 12 });
    await loading;
    expect(store.getState().list).toMatchObject({ status: "success", total: 12, error: null });
    expect(store.getState().list.items).toHaveLength(1);
  });

  it("ignores an older response that arrives after a newer one", async () => {
    const first = deferred<RecordListResponse>();
    const second = deferred<RecordListResponse>();
    const fetchRecords = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const store = createRecordStore(mockApi({ fetchRecords }));

    const older = store.loadRecords({ offset: 0 });
    const newer = store.loadRecords({ offset: 50 });
    second.resolve(listOf(makeRecord({ id: "new" })));
    await newer;
    first.resolve(listOf(makeRecord({ id: "old" })));
    await older;

    expect(store.getState().list.items.map((r) => r.id)).toEqual(["new"]);
    expect(store.getState().list.query).toEqual({ offset: 50 });
  });

  it("cancels the superseded request", async () => {
    const signals: AbortSignal[] = [];
    const fetchRecords = vi.fn((_params, options?: { signal?: AbortSignal }) => {
      if (options?.signal) signals.push(options.signal);
      return new Promise<RecordListResponse>(() => undefined);
    });
    const store = createRecordStore(mockApi({ fetchRecords }));

    void store.loadRecords();
    void store.loadRecords();

    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
  });

  it("ignores a stale failure after a newer success", async () => {
    const first = deferred<RecordListResponse>();
    const fetchRecords = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce(listOf(makeRecord()));
    const store = createRecordStore(mockApi({ fetchRecords }));

    const older = store.loadRecords();
    await store.loadRecords();
    first.reject(new ApiError("network", "offline"));
    await older;

    expect(store.getState().list.status).toBe("success");
    expect(store.getState().list.error).toBeNull();
  });

  it("keeps the previous items and exposes the error on failure", async () => {
    const fetchRecords = vi.fn().mockResolvedValueOnce(listOf(makeRecord())).mockRejectedValueOnce(new ApiError("http", "boom", { status: 503 }));
    const store = createRecordStore(mockApi({ fetchRecords }));

    await store.loadRecords();
    await store.loadRecords();

    const { list } = store.getState();
    expect(list.status).toBe("error");
    expect(list.error?.status).toBe(503);
    expect(list.items).toHaveLength(1);
  });

  it("reuses the current query when called without arguments", async () => {
    const fetchRecords = vi.fn(() => Promise.resolve(listOf()));
    const store = createRecordStore(mockApi({ fetchRecords }));
    await store.loadRecords({ sender: "מכבי", limit: 10 });
    await store.loadRecords();
    expect(fetchRecords).toHaveBeenLastCalledWith({ sender: "מכבי", limit: 10 }, expect.anything());
  });
});

describe("selectRecord", () => {
  it("shows the cached record immediately and then the fresh one", async () => {
    const fresh = deferred<RecordResponse>();
    const store = createRecordStore(
      mockApi({ fetchRecords: () => Promise.resolve(listOf(makeRecord({ id: "a" }))), fetchRecordById: () => fresh.promise }),
    );
    await store.loadRecords();

    const selecting = store.selectRecord("a");
    expect(store.getState().active).toMatchObject({ id: "a", status: "loading", record: { id: "a" } });

    fresh.resolve(makeRecord({ id: "a", updated_at: "2026-01-02T00:00:00+00:00" }));
    await selecting;
    expect(store.getState().active).toMatchObject({ status: "success", record: { updated_at: "2026-01-02T00:00:00+00:00" } });
  });

  it("ignores the response for a record that is no longer selected", async () => {
    const slow = deferred<RecordResponse>();
    const fetchRecordById = vi.fn().mockReturnValueOnce(slow.promise).mockResolvedValueOnce(makeRecord({ id: "b" }));
    const store = createRecordStore(mockApi({ fetchRecordById }));

    const first = store.selectRecord("a");
    await store.selectRecord("b");
    slow.resolve(makeRecord({ id: "a" }));
    await first;

    expect(store.getState().active).toMatchObject({ id: "b", record: { id: "b" } });
  });

  it("ignores a response that arrives after the selection is cleared", async () => {
    const slow = deferred<RecordResponse>();
    const store = createRecordStore(mockApi({ fetchRecordById: () => slow.promise }));

    const selecting = store.selectRecord("a");
    store.clearSelection();
    slow.resolve(makeRecord({ id: "a" }));
    await selecting;

    expect(store.getState().active).toEqual({ id: null, status: "idle", record: null, error: null });
  });

  it("exposes not-found errors", async () => {
    const store = createRecordStore(
      mockApi({ fetchRecordById: () => Promise.reject(new ApiError("http", "Record not found", { status: 404 })) }),
    );
    await store.selectRecord("missing");
    expect(store.getState().active.status).toBe("error");
    expect(store.getState().active.error?.isNotFound).toBe(true);
  });
});

describe("updateRecord", () => {
  it("updates the list and the active record without refetching", async () => {
    const updated = makeRecord({ id: "a", status: "approved", updated_at: "2026-01-02T00:00:00+00:00" });
    const api = mockApi({
      fetchRecords: vi.fn(() => Promise.resolve(listOf(makeRecord({ id: "a" }), makeRecord({ id: "b" })))),
      patchRecord: vi.fn(() => Promise.resolve(updated)),
    });
    const store = createRecordStore(api);
    await store.loadRecords();
    await store.selectRecord("a");

    const result = await store.updateRecord("a", { status: "approved" });

    expect(result).toEqual(updated);
    expect(store.getState().list.items.map((r) => r.status)).toEqual(["approved", "pending_review"]);
    expect(store.getState().active.record).toEqual(updated);
    expect(api.fetchRecords).toHaveBeenCalledTimes(1);
    expect(api.fetchRecordById).toHaveBeenCalledTimes(1);
    expect(store.getState().saves).toEqual({});
  });

  it("tracks the pending-save state and drops duplicate submissions", async () => {
    const response = deferred<RecordResponse>();
    const patchRecord = vi.fn(() => response.promise);
    const store = createRecordStore(mockApi({ patchRecord }));

    const first = store.updateRecord("a", { first_name: "Dana" });
    expect(store.isSaving("a")).toBe(true);
    expect(store.getState().saves.a).toEqual({ status: "saving", error: null });

    expect(await store.updateRecord("a", { first_name: "Dina" })).toBeNull();
    expect(patchRecord).toHaveBeenCalledTimes(1);

    response.resolve(makeRecord({ id: "a" }));
    await first;
    expect(store.isSaving("a")).toBe(false);
  });

  it("allows concurrent saves of different records", async () => {
    const patchRecord = vi.fn((id: string) => Promise.resolve(makeRecord({ id })));
    const store = createRecordStore(mockApi({ patchRecord }));
    await Promise.all([store.updateRecord("a", {}), store.updateRecord("b", {})]);
    expect(patchRecord).toHaveBeenCalledTimes(2);
  });

  it("records the error and allows a retry", async () => {
    const patchRecord = vi
      .fn()
      .mockRejectedValueOnce(new ApiError("http", "Cannot approve record: last_name_missing", { status: 400 }))
      .mockResolvedValueOnce(makeRecord({ id: "a", status: "approved" }));
    const store = createRecordStore(mockApi({ patchRecord }));

    expect(await store.updateRecord("a", { status: "approved" })).toBeNull();
    expect(store.getState().saves.a?.status).toBe("error");
    expect(store.getState().saves.a?.error?.status).toBe(400);

    expect(await store.updateRecord("a", { status: "approved", last_name: "Levi" })).not.toBeNull();
    expect(store.getState().saves.a).toBeUndefined();
  });

  it("is not overwritten by an older list response that resolves later", async () => {
    const slowList = deferred<RecordListResponse>();
    const fetchRecords = vi.fn().mockResolvedValueOnce(listOf(makeRecord({ id: "a" }))).mockReturnValueOnce(slowList.promise);
    const patched = makeRecord({ id: "a", first_name: { value: "Edited", confidence: "high", box: null, raw_value: null }, updated_at: "2026-01-03T00:00:00+00:00" });
    const store = createRecordStore(mockApi({ fetchRecords, patchRecord: () => Promise.resolve(patched) }));
    await store.loadRecords();

    const refreshing = store.loadRecords();
    await store.updateRecord("a", { first_name: "Edited" });
    slowList.resolve(listOf(makeRecord({ id: "a" })));
    await refreshing;

    expect(store.getState().list.items[0]?.first_name.value).toBe("Edited");
  });

  it("is not overwritten by an older detail response that resolves later", async () => {
    const slowDetail = deferred<RecordResponse>();
    const patched = makeRecord({ id: "a", status: "rejected", updated_at: "2026-01-03T00:00:00+00:00" });
    const store = createRecordStore(mockApi({ fetchRecordById: () => slowDetail.promise, patchRecord: () => Promise.resolve(patched) }));

    const selecting = store.selectRecord("a");
    await store.updateRecord("a", { status: "rejected" });
    slowDetail.resolve(makeRecord({ id: "a" }));
    await selecting;

    expect(store.getState().active.record?.status).toBe("rejected");
  });
});

describe("uploadDocument", () => {
  it("stores the uploaded records and refreshes the list", async () => {
    const uploaded = [makeRecord({ id: "p1" }), makeRecord({ id: "p2", page_number: 2 })];
    const api = mockApi({
      uploadDocuments: vi.fn(() => Promise.resolve(uploaded)),
      fetchRecords: vi.fn(() => Promise.resolve(listOf(...uploaded))),
    });
    const store = createRecordStore(api);

    const result = await store.uploadDocument(new File(["x"], "doc.pdf"));

    expect(result).toEqual(uploaded);
    expect(store.getState().upload).toEqual({ status: "success", records: uploaded, error: null });
    expect(api.fetchRecords).toHaveBeenCalledTimes(1);
  });

  it("drops a second upload while one is in flight", async () => {
    const response = deferred<RecordResponse[]>();
    const uploadDocuments = vi.fn(() => response.promise);
    const store = createRecordStore(mockApi({ uploadDocuments }));

    const first = store.uploadDocument(new File(["x"], "a.pdf"));
    expect(store.getState().upload.status).toBe("uploading");
    expect(await store.uploadDocument(new File(["y"], "b.pdf"))).toBeNull();
    expect(uploadDocuments).toHaveBeenCalledTimes(1);

    response.resolve([]);
    await first;
    expect(store.getState().upload.status).toBe("success");
  });

  it("exposes upload errors", async () => {
    const store = createRecordStore(
      mockApi({ uploadDocuments: () => Promise.reject(new ApiError("http", "Unsupported file type", { status: 415 })) }),
    );
    expect(await store.uploadDocument(new File(["x"], "a.txt"))).toBeNull();
    expect(store.getState().upload).toMatchObject({ status: "error", error: { status: 415 } });
  });

  it("wraps unexpected errors in ApiError", async () => {
    const store = createRecordStore(mockApi({ uploadDocuments: () => Promise.reject(new Error("bug")) }));
    await store.uploadDocument(new File(["x"], "a.pdf"));
    expect(store.getState().upload.error).toBeInstanceOf(ApiError);
  });
});

describe("subscriptions", () => {
  it("notifies subscribers and stops after unsubscribe or dispose", async () => {
    const store = createRecordStore(mockApi());
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);

    await store.loadRecords();
    const calls = listener.mock.calls.length;
    expect(calls).toBeGreaterThan(0);

    unsubscribe();
    await store.loadRecords();
    expect(listener).toHaveBeenCalledTimes(calls);

    store.dispose();
    const before = store.getState();
    await store.loadRecords();
    expect(store.getState()).toBe(before);
  });
});
