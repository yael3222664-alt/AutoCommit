import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { ApiError, createApiClient, recordImageUrl, type RecordApi } from "../src/services/api";
import { createRecordStore, type RecordStore, type RecordStoreState } from "../src/store/recordStore";
import {
  CONFIDENCE_LEVELS,
  EDITABLE_RECORD_STATUSES,
  RECORD_STATUSES,
  type BoundingBox,
  type CentralNumberItem,
  type CommitmentNumberItem,
  type FieldValue,
  type RecordListResponse,
  type RecordResponse,
  type RecordUpdatePayload,
} from "../src/types/record";
import { startBackend, writeSamplePdf, type BackendProcess } from "./backend";

/**
 * End-to-end flow: frontend API client + record store against the real FastAPI app and
 * a real SQLite file. Only the vision provider is faked (backend/tests/integration_server.py);
 * page 2 fails on the first run to verify page-level failure isolation.
 */

// Compile-time exhaustive key lists: a missing or extra key is a type error.
function keyList<T>(keys: { [K in keyof Required<T>]: true }): string[] {
  return Object.keys(keys).sort();
}

const RECORD_RESPONSE_KEYS = keyList<RecordResponse>({
  id: true, document_id: true, created_at: true, updated_at: true, page_number: true, image_path: true,
  first_name: true, last_name: true, id_number: true, central_numbers: true, received_date: true, sender: true,
  commitment_numbers: true, requires_manual_review: true, review_reasons: true, status: true,
});
const FIELD_VALUE_KEYS = keyList<FieldValue>({ value: true, confidence: true, box: true, raw_value: true });
const BOX_KEYS = keyList<BoundingBox>({ ymin: true, xmin: true, ymax: true, xmax: true, page_number: true });
const CENTRAL_KEYS = keyList<CentralNumberItem>({ value: true, confidence: true, source: true, box: true });
const COMMITMENT_KEYS = keyList<CommitmentNumberItem>({ value: true, confidence: true, label_type: true, box: true });
const LIST_KEYS = keyList<RecordListResponse>({ items: true, total: true, limit: true, offset: true });
const UPDATE_KEYS = keyList<RecordUpdatePayload>({
  first_name: true, last_name: true, id_number: true, received_date: true, sender: true,
  central_numbers: true, commitment_numbers: true, status: true,
});
const TEXT_FIELDS = ["first_name", "last_name", "id_number", "received_date", "sender"] as const;

interface OpenApiSchema {
  properties?: Record<string, OpenApiSchema>;
  enum?: string[];
  $ref?: string;
  allOf?: OpenApiSchema[];
  anyOf?: OpenApiSchema[];
  type?: string;
}

function countingClient(inner: RecordApi) {
  const calls = { upload: 0, list: 0, detail: 0, patch: 0 };
  const client: RecordApi = {
    uploadDocuments: (file, options) => (calls.upload++, inner.uploadDocuments(file, options)),
    fetchRecords: (params, options) => (calls.list++, inner.fetchRecords(params, options)),
    fetchRecordById: (id, options) => (calls.detail++, inner.fetchRecordById(id, options)),
    patchRecord: (id, payload, options) => (calls.patch++, inner.patchRecord(id, payload, options)),
  };
  return { client, calls };
}

function waitForState(
  store: RecordStore,
  predicate: (state: RecordStoreState) => boolean,
  timeoutMs = 10_000,
): Promise<RecordStoreState> {
  if (predicate(store.getState())) return Promise.resolve(store.getState());
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      unsubscribe();
      reject(new Error("Timed out waiting for store state"));
    }, timeoutMs);
    const unsubscribe = store.subscribe(() => {
      if (!predicate(store.getState())) return;
      clearTimeout(timer);
      unsubscribe();
      resolve(store.getState());
    });
  });
}

function byPage(records: readonly RecordResponse[], page: number): RecordResponse {
  const record = records.find((item) => item.page_number === page);
  if (!record) throw new Error(`No record for page ${page}`);
  return record;
}

function saveError(store: RecordStore, id: string): ApiError | null {
  return store.getState().saves[id]?.error ?? null;
}

describe("end-to-end record lifecycle", () => {
  let workDir: string;
  let database: string;
  let samplePdf: File;
  let backend: BackendProcess;
  let store: RecordStore;
  let calls: ReturnType<typeof countingClient>["calls"];
  const ids: Record<number, string> = {};

  async function rawJson<T>(urlPath: string): Promise<T> {
    const response = await fetch(`${backend.baseUrl}${urlPath}`);
    expect(response.status).toBe(200);
    return (await response.json()) as T;
  }

  beforeAll(async () => {
    workDir = await mkdtemp(path.join(tmpdir(), "autocommit-e2e-"));
    database = path.join(workDir, "e2e.db");
    const pdfPath = path.join(workDir, "sample.pdf");
    await writeSamplePdf(pdfPath);
    samplePdf = new File([await readFile(pdfPath)], "sample.pdf", { type: "application/pdf" });
    backend = await startBackend({ database, failPages: [2] });
    const counted = countingClient(createApiClient({ baseUrl: backend.baseUrl }));
    calls = counted.calls;
    store = createRecordStore(counted.client);
  });

  afterAll(async () => {
    store?.dispose();
    await backend?.stop();
    if (workDir) await rm(workDir, { recursive: true, force: true });
  });

  it("backend OpenAPI contract matches the frontend types", async () => {
    const openapi = await rawJson<{ components: { schemas: Record<string, OpenApiSchema> } }>("/openapi.json");
    const schemas = openapi.components.schemas;
    const schema = (name: string): OpenApiSchema => {
      const found = schemas[name] ?? schemas[`${name}-Output`];
      if (!found) throw new Error(`OpenAPI schema ${name} not found`);
      return found;
    };
    const resolve = (node: OpenApiSchema): OpenApiSchema => {
      if (node.$ref) return resolve(schema(node.$ref.split("/").pop() ?? ""));
      if (node.allOf?.length === 1 && node.allOf[0]) return resolve(node.allOf[0]);
      const nonNull = node.anyOf?.filter((option) => option.type !== "null");
      if (nonNull?.length === 1 && nonNull[0]) return resolve(nonNull[0]);
      return node;
    };
    const keys = (name: string) => Object.keys(schema(name).properties ?? {}).sort();
    const enumOf = (name: string, property: string) => {
      const node = schema(name).properties?.[property];
      if (!node) throw new Error(`${name}.${property} not found`);
      return resolve(node).enum ?? null;
    };

    expect(keys("RecordResponse")).toEqual(RECORD_RESPONSE_KEYS);
    expect(keys("FieldValue")).toEqual(FIELD_VALUE_KEYS);
    expect(keys("BoundingBox")).toEqual(BOX_KEYS);
    expect(keys("CentralNumberItem")).toEqual(CENTRAL_KEYS);
    expect(keys("CommitmentNumberItem")).toEqual(COMMITMENT_KEYS);
    expect(keys("RecordListResponse")).toEqual(LIST_KEYS);
    expect(keys("RecordUpdateSchema")).toEqual(UPDATE_KEYS);
    expect(enumOf("FieldValue", "confidence")).toEqual([...CONFIDENCE_LEVELS]);
    expect(enumOf("RecordResponse", "status")).toEqual([...RECORD_STATUSES]);
    expect(enumOf("RecordUpdateSchema", "status")).toEqual([...EDITABLE_RECORD_STATUSES]);
  });

  it("uploads a 3-page PDF: one record per page, grounded fields, failed page isolated", async () => {
    const uploadStatuses: string[] = [];
    const unsubscribe = store.subscribe(() => uploadStatuses.push(store.getState().upload.status));

    const upload = store.uploadDocument(samplePdf);
    expect(await store.uploadDocument(samplePdf)).toBeNull(); // duplicate in-flight upload rejected
    const records = await upload;
    unsubscribe();

    expect(records).not.toBeNull();
    expect(calls.upload).toBe(1);
    expect(uploadStatuses[0]).toBe("uploading");
    expect(uploadStatuses).toContain("success");
    expect(records?.map((r) => r.page_number)).toEqual([1, 2, 3]);
    expect(new Set(records?.map((r) => r.id)).size).toBe(3);
    expect(new Set(records?.map((r) => r.document_id)).size).toBe(1);
    for (const record of records ?? []) ids[record.page_number] = record.id;

    const page1 = byPage(records ?? [], 1);
    expect(page1.status).toBe("pending_review");
    expect(page1.requires_manual_review).toBe(true);
    expect(page1.review_reasons).toEqual(["last_name_missing", "flagged_by_model"]);
    expect(page1.first_name).toEqual({
      value: "דנה", confidence: "high", raw_value: null, // raw_value is kept only for normalized fields
      box: { ymin: 0.1, xmin: 0.55, ymax: 0.14, xmax: 0.9, page_number: 1 },
    });
    expect(page1.last_name).toEqual({ value: null, confidence: "low", box: null, raw_value: null });
    expect(page1.id_number.value).toBe("012345674");
    expect(page1.received_date.value).toBe("2024-03-05");
    expect(page1.received_date.raw_value).toBe("05/03/2024");
    expect(page1.sender.value).toBe("כללית");
    expect(page1.central_numbers).toEqual([
      { value: "001234", confidence: "high", source: "sticker",
        box: { ymin: 0.3, xmin: 0.05, ymax: 0.36, xmax: 0.25, page_number: 1 } },
    ]);
    expect(page1.commitment_numbers[0]?.value).toBe("0098761");

    const page2 = byPage(records ?? [], 2);
    expect(page2.status).toBe("extraction_failed");
    expect(page2.requires_manual_review).toBe(true);
    expect(page2.review_reasons).toEqual(["provider_error"]);
    for (const name of TEXT_FIELDS) {
      expect(page2[name]).toEqual({ value: null, confidence: "low", box: null, raw_value: null });
    }
    expect(page2.central_numbers).toEqual([]);

    const page3 = byPage(records ?? [], 3);
    expect(page3.status).toBe("pending_review");
    expect(page3.requires_manual_review).toBe(false);
    expect(page3.review_reasons).toEqual([]);
    expect(page3.id_number.box?.page_number).toBe(3);

    // The store refreshes the list after an upload.
    const state = await waitForState(store, (s) => s.list.status === "success" && s.list.items.length === 3);
    expect(state.list.total).toBe(3);
  });

  it("serves the 300 DPI page image each record's boxes refer to, failed pages included", async () => {
    const images: Buffer[] = [];
    for (const page of [1, 2, 3]) {
      const record = await rawJson<RecordResponse>(`/api/records/${ids[page]}`);
      expect(record.image_path).toBe(`${record.document_id}/${page}.png`);
      const response = await fetch(recordImageUrl(ids[page]!, backend.baseUrl));
      expect(response.status).toBe(200);
      expect(response.headers.get("content-type")).toBe("image/png");
      const png = Buffer.from(await response.arrayBuffer());
      expect(png.subarray(1, 4).toString("ascii")).toBe("PNG");
      // 300x400 pt rendered at 300 DPI; IHDR holds big-endian width and height at bytes 16..24.
      expect(png.readUInt32BE(16)).toBe(1250);
      expect([1666, 1667]).toContain(png.readUInt32BE(20));
      images.push(png);
    }
    expect(new Set(images.map((png) => png.toString("base64"))).size).toBe(3);
    expect((await fetch(recordImageUrl("missing", backend.baseUrl))).status).toBe(404);
  });

  it("raw API responses carry exactly the fields the frontend types declare", async () => {
    const list = await rawJson<Record<string, unknown> & { items: Record<string, unknown>[] }>("/api/records");
    expect(Object.keys(list).sort()).toEqual(LIST_KEYS);
    expect(list.items).toHaveLength(3);
    for (const item of list.items) {
      expect(Object.keys(item).sort()).toEqual(RECORD_RESPONSE_KEYS);
      for (const name of TEXT_FIELDS) {
        const field = item[name] as Record<string, unknown>;
        expect(Object.keys(field).sort()).toEqual(FIELD_VALUE_KEYS);
        if (field.box) expect(Object.keys(field.box as object).sort()).toEqual(BOX_KEYS);
      }
      for (const central of item.central_numbers as Record<string, unknown>[]) {
        expect(Object.keys(central).sort()).toEqual(CENTRAL_KEYS);
      }
      for (const commitment of item.commitment_numbers as Record<string, unknown>[]) {
        expect(Object.keys(commitment).sort()).toEqual(COMMITMENT_KEYS);
      }
    }
  });

  it("enforces approval rules: incomplete and failed records cannot be approved", async () => {
    const page1 = ids[1] ?? "";
    const page2 = ids[2] ?? "";
    const before = store.getState().list.items;

    expect(await store.updateRecord(page1, { status: "approved" })).toBeNull();
    expect(saveError(store, page1)?.status).toBe(400);
    expect(saveError(store, page1)?.message).toContain("last_name_missing");

    expect(await store.updateRecord(page2, { status: "approved" })).toBeNull();
    expect(saveError(store, page2)?.status).toBe(400);

    expect(store.getState().list.items).toEqual(before);
  });

  it("PATCH updates the active record and cached list without refetching", async () => {
    const page1 = ids[1] ?? "";
    await store.selectRecord(page1);
    expect(store.getState().active).toMatchObject({ id: page1, status: "success" });
    const listCalls = calls.list;
    const detailCalls = calls.detail;

    const updated = await store.updateRecord(page1, { last_name: "אברהם" });

    expect(updated?.last_name).toMatchObject({ value: "אברהם", confidence: "high" });
    expect(updated?.first_name.value).toBe("דנה"); // omitted fields are untouched
    // The field's reason is cleared; the page-level model flag stays until a reviewer decides.
    expect(updated?.review_reasons).toEqual(["flagged_by_model"]);
    expect(updated?.requires_manual_review).toBe(true);
    const state = store.getState();
    expect(state.active.record).toEqual(updated);
    expect(byPage(state.list.items, 1)).toEqual(updated);
    expect(state.saves[page1]).toBeUndefined();
    expect(calls.list).toBe(listCalls);
    expect(calls.detail).toBe(detailCalls);
    expect(await rawJson<RecordResponse>(`/api/records/${page1}`)).toEqual(updated);
  });

  it("distinguishes explicit null from omitted fields and rejects invalid values", async () => {
    const page3 = ids[3] ?? "";
    const cleared = await store.updateRecord(page3, { sender: null });
    expect(cleared?.sender.value).toBeNull();
    expect(cleared?.sender.box).not.toBeNull(); // location on the page is kept for the reviewer
    expect(cleared?.first_name.value).toBe("יוסי");
    expect(cleared?.review_reasons).toEqual(["sender_missing"]);
    expect(cleared?.requires_manual_review).toBe(true);

    expect(await store.updateRecord(page3, { status: "approved" })).toBeNull();
    expect(saveError(store, page3)?.status).toBe(400);

    expect(await store.updateRecord(page3, { received_date: "31/02/2024" })).toBeNull();
    expect(saveError(store, page3)?.status).toBe(422);
    expect(byPage(store.getState().list.items, 3)).toEqual(cleared);

    const restored = await store.updateRecord(page3, { sender: "מכבי" });
    expect(restored?.sender).toMatchObject({ value: "מכבי", confidence: "high" });
    expect(restored?.requires_manual_review).toBe(false);
    expect(store.getState().saves[page3]).toBeUndefined();
  });

  it("prevents duplicate in-flight saves of the same record", async () => {
    const page3 = ids[3] ?? "";
    const patches = calls.patch;
    const [first, second] = await Promise.all([
      store.updateRecord(page3, { first_name: "יוסף" }),
      store.updateRecord(page3, { first_name: "שגוי" }),
    ]);
    expect(first?.first_name.value).toBe("יוסף");
    expect(second).toBeNull();
    expect(calls.patch).toBe(patches + 1);
    expect(byPage(store.getState().list.items, 3).first_name.value).toBe("יוסף");
  });

  it("approves a complete record; approved records are locked until reopened", async () => {
    const page1 = ids[1] ?? "";
    const approved = await store.updateRecord(page1, { status: "approved" });
    expect(approved?.status).toBe("approved");
    expect(approved?.requires_manual_review).toBe(false);
    expect(store.getState().active.record?.status).toBe("approved");
    expect(byPage(store.getState().list.items, 1).status).toBe("approved");

    expect(await store.updateRecord(page1, { first_name: "אחר" })).toBeNull();
    expect(saveError(store, page1)?.status).toBe(400);
    expect(store.getState().active.record).toEqual(approved);

    const filtered = await rawJson<RecordListResponse>("/api/records?status=approved");
    expect(filtered.items.map((r) => r.id)).toEqual([page1]);
  });

  it("ignores superseded list and detail responses and reports 404s", async () => {
    const listStatuses: string[] = [];
    const unsubscribe = store.subscribe(() => listStatuses.push(store.getState().list.status));
    await Promise.all([store.loadRecords({ status: "approved" }), store.loadRecords({})]);
    unsubscribe();
    expect(listStatuses[0]).toBe("loading");
    expect(store.getState().list).toMatchObject({ status: "success", query: {}, total: 3 });
    expect(store.getState().list.items).toHaveLength(3);

    await Promise.all([store.selectRecord(ids[1] ?? ""), store.selectRecord(ids[3] ?? "")]);
    expect(store.getState().active).toMatchObject({ id: ids[3], status: "success", error: null });
    expect(store.getState().active.record?.id).toBe(ids[3]);

    await store.selectRecord("00000000-0000-0000-0000-000000000000");
    const active = store.getState().active;
    expect(active.status).toBe("error");
    expect(active.error?.isNotFound).toBe(true);
    store.clearSelection();
  });

  async function pageImage(id: string): Promise<string> {
    const response = await fetch(recordImageUrl(id, backend.baseUrl));
    expect(response.status).toBe(200);
    return Buffer.from(await response.arrayBuffer()).toString("base64");
  }

  it("persists records across a server restart and keeps edits on re-upload", async () => {
    const snapshot = await rawJson<RecordListResponse>("/api/records");
    const imagesBefore = await Promise.all([1, 2, 3].map((page) => pageImage(ids[page]!)));
    await backend.stop();

    // While the server is down, the store reports a network error but keeps cached records.
    await store.loadRecords();
    const offline = store.getState().list;
    expect(offline.status).toBe("error");
    expect(offline.error?.kind).toBe("network");
    expect(offline.items).toHaveLength(3);

    backend = await startBackend({ database, port: backend.port }); // no failing pages this time
    expect(await rawJson<RecordListResponse>("/api/records")).toEqual(snapshot);
    expect(await rawJson<RecordResponse>(`/api/records/${ids[1]}`)).toEqual(byPage(snapshot.items, 1));
    expect(await Promise.all([1, 2, 3].map((page) => pageImage(ids[page]!)))).toEqual(imagesBefore);
    await store.loadRecords();
    expect(store.getState().list).toMatchObject({ status: "success", error: null, total: 3 });

    const reuploaded = await store.uploadDocument(samplePdf);
    expect(reuploaded?.map((r) => r.id)).toEqual([ids[1], ids[2], ids[3]]);
    expect(byPage(reuploaded ?? [], 1)).toEqual(byPage(snapshot.items, 1)); // approval and edits kept
    expect(byPage(reuploaded ?? [], 3)).toEqual(byPage(snapshot.items, 3));
    const retried = byPage(reuploaded ?? [], 2); // the failed, unedited page is re-extracted
    expect(retried.status).toBe("pending_review");
    expect(retried.id_number.value).toBe("000000018");
    expect(retried.received_date.value).toBe("2024-04-12");
    expect(retried.review_reasons).toEqual([]);

    const state = await waitForState(store, (s) => s.list.status === "success" && byPage(s.list.items, 2).status === "pending_review");
    expect(state.list.total).toBe(3);
  });
});

describe("records saved before page images existed", () => {
  let workDir: string;
  let samplePdf: File;
  let backend: BackendProcess;

  beforeAll(async () => {
    workDir = await mkdtemp(path.join(tmpdir(), "autocommit-legacy-"));
    const pdfPath = path.join(workDir, "sample.pdf");
    await writeSamplePdf(pdfPath);
    samplePdf = new File([await readFile(pdfPath)], "sample.pdf", { type: "application/pdf" });
  });

  afterAll(async () => {
    await backend?.stop();
    if (workDir) await rm(workDir, { recursive: true, force: true });
  });

  it("keeps edits and decisions, then links images on re-upload", async () => {
    const database = path.join(workDir, "legacy.db");
    backend = await startBackend({ database, pageImages: false });
    const client = createApiClient({ baseUrl: backend.baseUrl });
    const legacy = await client.uploadDocuments(samplePdf);
    expect(legacy.map((r) => r.image_path)).toEqual([null, null, null]);
    const [page1, page2] = [byPage(legacy, 1), byPage(legacy, 2)];
    expect((await fetch(recordImageUrl(page1.id, backend.baseUrl))).status).toBe(404);

    const edited = await client.patchRecord(page1.id, { last_name: "כהן" });
    const approved = await client.patchRecord(edited.id, { status: "approved" });
    const rejected = await client.patchRecord(page2.id, { status: "rejected" });
    await backend.stop();

    backend = await startBackend({ database, port: backend.port }); // current version: images enabled
    const restarted = createApiClient({ baseUrl: backend.baseUrl });
    expect(await restarted.fetchRecordById(page1.id)).toEqual(approved); // legacy record still readable

    const reuploaded = await restarted.uploadDocuments(samplePdf);
    expect(reuploaded.map((r) => r.id)).toEqual(legacy.map((r) => r.id));
    const strip = (record: RecordResponse) => ({ ...record, image_path: null, updated_at: "" });
    expect(strip(byPage(reuploaded, 1))).toEqual(strip(approved));
    expect(strip(byPage(reuploaded, 2))).toEqual(strip(rejected));
    for (const record of reuploaded) {
      expect(record.image_path).toBe(`${record.document_id}/${record.page_number}.png`);
      const response = await fetch(recordImageUrl(record.id, backend.baseUrl));
      expect(response.status).toBe(200);
      expect(response.headers.get("content-type")).toBe("image/png");
    }
  });

  it("never serves files outside the image store", async () => {
    for (const hostile of ["..%2F..%2Fetc%2Fpasswd", "%2e%2e", "..", "missing"]) {
      const response = await fetch(`${backend.baseUrl}/api/records/${hostile}/image`);
      expect(response.status, hostile).toBe(404);
      const body = await response.text();
      expect(body).not.toMatch(/root:|\/home\/|\/etc\//);
    }
  });
});
