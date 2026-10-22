import type { RecordListResponse, RecordQueryParams, RecordResponse, RecordUpdatePayload } from "../types/record";
import { notifyUnauthorized } from "./auth";
import { parseRecordList, parseRecordListResponse, parseRecordResponse, ResponseShapeError } from "./responseValidation";

export type ApiErrorKind = "network" | "timeout" | "aborted" | "http" | "malformed_response";

export interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
}

/** Single error type surfaced by the API client. `status` is set for HTTP errors. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  /** FastAPI 422 validation issues, when present. */
  readonly issues: ValidationIssue[];

  constructor(kind: ApiErrorKind, message: string, options: { status?: number; issues?: ValidationIssue[]; cause?: unknown } = {}) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.kind = kind;
    this.status = options.status ?? null;
    this.issues = options.issues ?? [];
  }

  get isNotFound(): boolean {
    return this.kind === "http" && this.status === 404;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export interface RequestOptions {
  signal?: AbortSignal;
}

export interface RecordApi {
  uploadDocuments(file: File, options?: RequestOptions): Promise<RecordResponse[]>;
  fetchRecords(params?: RecordQueryParams, options?: RequestOptions): Promise<RecordListResponse>;
  fetchRecordById(id: string, options?: RequestOptions): Promise<RecordResponse>;
  patchRecord(id: string, payload: RecordUpdatePayload, options?: RequestOptions): Promise<RecordResponse>;
}

export interface ApiClientOptions {
  /** Called when the server rejects the session (HTTP 401). */
  onUnauthorized?: () => void;
  /** Backend origin; empty string means same origin (Vite proxy in development). */
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  /** Uploads run vision extraction for every page and need a longer budget. */
  uploadTimeoutMs?: number;
}

export const DEFAULT_TIMEOUT_MS = 30_000;
export const DEFAULT_UPLOAD_TIMEOUT_MS = 10 * 60_000;

export function buildQueryString(params: RecordQueryParams = {}): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.append(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

function describeHttpError(status: number, body: unknown): { message: string; issues: ValidationIssue[] } {
  const detail = typeof body === "object" && body !== null ? (body as { detail?: unknown }).detail : undefined;
  if (typeof detail === "string") return { message: detail, issues: [] };
  if (Array.isArray(detail)) {
    const issues = detail
      .filter((item): item is ValidationIssue => typeof item === "object" && item !== null && typeof item.msg === "string")
      .map((item) => ({ loc: Array.isArray(item.loc) ? item.loc : [], msg: item.msg }));
    if (issues.length > 0) {
      return { message: issues.map((issue) => `${issue.loc.slice(1).join(".") || "request"}: ${issue.msg}`).join("; "), issues };
    }
  }
  return { message: `Request failed with status ${status}`, issues: [] };
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

export function createApiClient(options: ApiClientOptions = {}): RecordApi {
  const baseUrl = (options.baseUrl ?? "").replace(/\/+$/, "");
  const fetchImpl = options.fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const uploadTimeoutMs = options.uploadTimeoutMs ?? DEFAULT_UPLOAD_TIMEOUT_MS;

  async function request<T>(
    path: string,
    init: RequestInit,
    parse: (body: unknown) => T,
    { signal, timeout = timeoutMs }: RequestOptions & { timeout?: number } = {},
  ): Promise<T> {
    if (signal?.aborted) throw new ApiError("aborted", "Request was cancelled");

    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeout);
    const onAbort = () => controller.abort();
    signal?.addEventListener("abort", onAbort, { once: true });

    try {
      let response: Response;
      try {
        response = await fetchImpl(`${baseUrl}/api${path}`, { ...init, signal: controller.signal });
      } catch (cause) {
        if (timedOut) throw new ApiError("timeout", `Request timed out after ${timeout} ms`, { cause });
        if (controller.signal.aborted) throw new ApiError("aborted", "Request was cancelled", { cause });
        throw new ApiError("network", "Could not reach the server", { cause });
      }

      const body = await readJson(response);
      if (response.status === 401) options.onUnauthorized?.();
      if (!response.ok) {
        const { message, issues } = describeHttpError(response.status, body);
        throw new ApiError("http", message, { status: response.status, issues });
      }
      try {
        return parse(body);
      } catch (cause) {
        const message = cause instanceof ResponseShapeError ? cause.message : "Unexpected response from server";
        throw new ApiError("malformed_response", message, { status: response.status, cause });
      }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (timedOut) throw new ApiError("timeout", `Request timed out after ${timeout} ms`, { cause: error });
      if (controller.signal.aborted) throw new ApiError("aborted", "Request was cancelled", { cause: error });
      throw new ApiError("network", "Could not read the server response", { cause: error });
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }

  return {
    uploadDocuments(file, requestOptions = {}) {
      const form = new FormData();
      form.append("file", file, file.name);
      return request("/documents/upload", { method: "POST", body: form }, parseRecordList, {
        ...requestOptions,
        timeout: uploadTimeoutMs,
      });
    },

    fetchRecords(params = {}, requestOptions = {}) {
      return request(`/records${buildQueryString(params)}`, { method: "GET" }, parseRecordListResponse, requestOptions);
    },

    fetchRecordById(id, requestOptions = {}) {
      return request(`/records/${encodeURIComponent(id)}`, { method: "GET" }, parseRecordResponse, requestOptions);
    },

    patchRecord(id, payload, requestOptions = {}) {
      return request(
        `/records/${encodeURIComponent(id)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
        parseRecordResponse,
        requestOptions,
      );
    },
  };
}

const DEFAULT_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export const api: RecordApi = createApiClient({ baseUrl: DEFAULT_BASE_URL, onUnauthorized: notifyUnauthorized });

/** URL of the page image a record was extracted from (GET /api/records/{id}/image). */
export function recordImageUrl(id: string, baseUrl: string = DEFAULT_BASE_URL): string {
  return `${baseUrl.replace(/\/+$/, "")}/api/records/${encodeURIComponent(id)}/image`;
}
