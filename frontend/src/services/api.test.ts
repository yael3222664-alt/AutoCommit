import { describe, expect, it, vi } from "vitest";

import { makeRecord } from "../test/fixtures";
import { ApiError, buildQueryString, createApiClient, recordImageUrl } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function clientReturning(response: Response | (() => Promise<Response>), options: { timeoutMs?: number } = {}) {
  const fetchImpl = vi.fn<typeof fetch>(() => (typeof response === "function" ? response() : Promise.resolve(response)));
  return { fetchImpl, client: createApiClient({ baseUrl: "http://api.test/", fetchImpl, ...options }) };
}

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected promise to reject");
}

function requestOf(fetchImpl: ReturnType<typeof vi.fn<typeof fetch>>): { url: string; init: RequestInit } {
  const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

describe("serialization", () => {
  it("uploads the file as multipart form data and parses the records", async () => {
    const { fetchImpl, client } = clientReturning(jsonResponse([makeRecord(), makeRecord({ id: "rec-2", page_number: 2 })]));
    const file = new File([new Uint8Array([37, 80, 68, 70])], "form.pdf", { type: "application/pdf" });

    const records = await client.uploadDocuments(file);

    const { url, init } = requestOf(fetchImpl);
    expect(url).toBe("http://api.test/api/documents/upload");
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect((body.get("file") as File).name).toBe("form.pdf");
    expect(init.headers).toBeUndefined();
    expect(records.map((record) => record.page_number)).toEqual([1, 2]);
  });

  it("encodes list filters and skips unset parameters", async () => {
    const { fetchImpl, client } = clientReturning(jsonResponse({ items: [makeRecord()], total: 7, limit: 1, offset: 3 }));

    const result = await client.fetchRecords({
      status: "pending_review",
      sender: "שיבא (תל השומר)",
      requires_manual_review: false,
      created_from: "2026-01-01T00:00:00Z",
      limit: 1,
      offset: 3,
    });

    const url = new URL(requestOf(fetchImpl).url);
    expect(url.pathname).toBe("/api/records");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      status: "pending_review",
      sender: "שיבא (תל השומר)",
      requires_manual_review: "false",
      created_from: "2026-01-01T00:00:00Z",
      limit: "1",
      offset: "3",
    });
    expect(result.total).toBe(7);
    expect(result.items[0]?.id_number.value).toBe("000000018");
  });

  it("builds an empty query string when no parameters are set", () => {
    expect(buildQueryString({})).toBe("");
    expect(buildQueryString({ sender: "", offset: 0 })).toBe("?offset=0");
  });

  it("escapes record ids in the path", async () => {
    const { fetchImpl, client } = clientReturning(jsonResponse(makeRecord({ id: "a/b?c" })));
    await client.fetchRecordById("a/b?c");
    expect(requestOf(fetchImpl).url).toBe("http://api.test/api/records/a%2Fb%3Fc");
  });

  it("keeps leading zeros in identifiers", async () => {
    const { client } = clientReturning(jsonResponse(makeRecord()));
    const record = await client.fetchRecordById("rec-1");
    expect(record.id_number.value).toBe("000000018");
    expect(record.central_numbers[0]?.value).toBe("000123");
    expect(record.commitment_numbers[0]?.value).toBe("0070");
  });
});

describe("partial PATCH payloads", () => {
  it("sends only supplied keys and keeps explicit nulls", async () => {
    const { fetchImpl, client } = clientReturning(jsonResponse(makeRecord()));

    await client.patchRecord("rec-1", { last_name: null, id_number: "000000018" });

    const { url, init } = requestOf(fetchImpl);
    expect(url).toBe("http://api.test/api/records/rec-1");
    expect(init.method).toBe("PATCH");
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ last_name: null, id_number: "000000018" });
  });

  it("sends an empty object when nothing changed", async () => {
    const { fetchImpl, client } = clientReturning(jsonResponse(makeRecord()));
    await client.patchRecord("rec-1", {});
    expect(requestOf(fetchImpl).init.body).toBe("{}");
  });

  it("serializes list replacement and status change", async () => {
    const { fetchImpl, client } = clientReturning(jsonResponse(makeRecord({ status: "approved" })));
    await client.patchRecord("rec-1", { central_numbers: [{ value: "000123" }], commitment_numbers: null, status: "approved" });
    expect(JSON.parse(requestOf(fetchImpl).init.body as string)).toEqual({
      central_numbers: [{ value: "000123" }],
      commitment_numbers: null,
      status: "approved",
    });
  });
});

describe("HTTP errors", () => {
  it.each([400, 404, 409, 413, 415, 503])("maps status %i with a string detail", async (status) => {
    const { client } = clientReturning(jsonResponse({ detail: `failure ${status}` }, status));
    const error = await captureError(client.fetchRecordById("rec-1"));
    expect(error.kind).toBe("http");
    expect(error.status).toBe(status);
    expect(error.message).toBe(`failure ${status}`);
    expect(error.isNotFound).toBe(status === 404);
  });

  it("exposes FastAPI 422 validation issues", async () => {
    const detail = [
      { loc: ["body", "id_number"], msg: "Input should be a valid string", type: "string_type" },
      { loc: ["body", "page_number"], msg: "Extra inputs are not permitted", type: "extra_forbidden" },
    ];
    const { client } = clientReturning(jsonResponse({ detail }, 422));

    const error = await captureError(client.patchRecord("rec-1", { first_name: "x" }));

    expect(error.status).toBe(422);
    expect(error.issues).toEqual([
      { loc: ["body", "id_number"], msg: "Input should be a valid string" },
      { loc: ["body", "page_number"], msg: "Extra inputs are not permitted" },
    ]);
    expect(error.message).toBe("id_number: Input should be a valid string; page_number: Extra inputs are not permitted");
  });

  it("falls back to a generic message for non-JSON error bodies", async () => {
    const { client } = clientReturning(new Response("<html>Bad gateway</html>", { status: 502 }));
    const error = await captureError(client.fetchRecords());
    expect(error.status).toBe(502);
    expect(error.message).toBe("Request failed with status 502");
  });
});

describe("malformed responses", () => {
  it.each([
    ["invalid JSON", new Response("{not json", { status: 200 })],
    ["empty body", new Response("", { status: 200 })],
    ["missing fields", jsonResponse({ id: "rec-1" })],
    ["numeric id number", jsonResponse({ ...makeRecord(), id_number: { value: 18, confidence: "high" } })],
    ["unknown status", jsonResponse({ ...makeRecord(), status: "archived" })],
    ["out-of-range box", jsonResponse({ ...makeRecord(), sender: { value: null, confidence: "low", box: { ymin: 0, xmin: 0, ymax: 1.5, xmax: 1 } } })],
  ])("rejects %s", async (_label, response) => {
    const { client } = clientReturning(response);
    const error = await captureError(client.fetchRecordById("rec-1"));
    expect(error.kind).toBe("malformed_response");
  });

  it("rejects a list response with an invalid item", async () => {
    const { client } = clientReturning(jsonResponse({ items: [makeRecord(), { id: 1 }], total: 2, limit: 50, offset: 0 }));
    const error = await captureError(client.fetchRecords());
    expect(error.kind).toBe("malformed_response");
    expect(error.message).toContain("response.items[1]");
  });
});

describe("network failures", () => {
  it("reports network errors", async () => {
    const { client } = clientReturning(() => Promise.reject(new TypeError("Failed to fetch")));
    const error = await captureError(client.fetchRecords());
    expect(error.kind).toBe("network");
    expect(error.status).toBeNull();
  });

  it("times out a request that never completes", async () => {
    const fetchImpl = vi.fn<typeof fetch>(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    const client = createApiClient({ fetchImpl, timeoutMs: 10 });

    const error = await captureError(client.fetchRecordById("rec-1"));

    expect(error.kind).toBe("timeout");
    expect(fetchImpl.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });

  it("uses the longer upload timeout for uploads", async () => {
    vi.useFakeTimers();
    try {
      const fetchImpl = vi.fn<typeof fetch>(
        (_url, init) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
          }),
      );
      const client = createApiClient({ fetchImpl, timeoutMs: 10, uploadTimeoutMs: 1_000 });
      const pending = captureError(client.uploadDocuments(new File(["x"], "a.png")));

      await vi.advanceTimersByTimeAsync(500);
      expect(fetchImpl.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(600);
      expect((await pending).kind).toBe("timeout");
    } finally {
      vi.useRealTimers();
    }
  });

  it("distinguishes caller cancellation from timeouts", async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn<typeof fetch>(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    const client = createApiClient({ fetchImpl });
    const pending = captureError(client.fetchRecords({}, { signal: controller.signal }));

    controller.abort();

    expect((await pending).kind).toBe("aborted");
  });

  it("does not send a request when already cancelled", async () => {
    const controller = new AbortController();
    controller.abort();
    const { fetchImpl, client } = clientReturning(jsonResponse(makeRecord()));

    const error = await captureError(client.fetchRecordById("rec-1", { signal: controller.signal }));

    expect(error.kind).toBe("aborted");
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("recordImageUrl", () => {
  it("builds the record image endpoint and encodes the id", () => {
    expect(recordImageUrl("abc-1", "")).toBe("/api/records/abc-1/image");
    expect(recordImageUrl("a/b", "http://host:8000/")).toBe("http://host:8000/api/records/a%2Fb/image");
  });
});
