import { describe, expect, it, vi } from "vitest";

import { createAuthApi, notifyUnauthorized, onUnauthorized } from "./auth";
import { createApiClient } from "./api";

function respond(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

describe("createAuthApi", () => {
  it("reads the session state", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(respond(200, { auth_enabled: true, authenticated: false }));
    const auth = createAuthApi({ baseUrl: "http://host/", fetchImpl });
    await expect(auth.getSession()).resolves.toEqual({ authEnabled: true, authenticated: false });
    expect(fetchImpl).toHaveBeenCalledWith("http://host/api/auth/session", expect.objectContaining({ method: "GET" }));
  });

  it("rejects a malformed session response", async () => {
    const auth = createAuthApi({ fetchImpl: vi.fn().mockResolvedValue(respond(200, { authenticated: "yes" })) });
    await expect(auth.getSession()).rejects.toThrow("Unexpected session response");
  });

  it("posts the password as JSON and maps the outcome", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(respond(204));
    const auth = createAuthApi({ fetchImpl });
    await expect(auth.login("secret password")).resolves.toBe("ok");
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit & { body: string }];
    expect(url).toBe("/api/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ password: "secret password" });
  });

  it.each([
    [401, "invalid"],
    [429, "rate_limited"],
    [500, "error"],
  ])("maps HTTP %i to %s", async (status, expected) => {
    const auth = createAuthApi({ fetchImpl: vi.fn().mockResolvedValue(respond(status)) });
    await expect(auth.login("x")).resolves.toBe(expected);
  });

  it("reports network failures as an error result", async () => {
    const auth = createAuthApi({ fetchImpl: vi.fn().mockRejectedValue(new TypeError("offline")) });
    await expect(auth.login("x")).resolves.toBe("error");
  });
});

describe("unauthorized notifications", () => {
  it("notifies subscribers until they unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    notifyUnauthorized();
    unsubscribe();
    notifyUnauthorized();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("is triggered by API responses with status 401 only", async () => {
    const onUnauthorizedSpy = vi.fn();
    const fetchImpl = vi.fn().mockResolvedValue(respond(401, { detail: "Authentication required" }));
    const client = createApiClient({ fetchImpl, onUnauthorized: onUnauthorizedSpy });
    await expect(client.fetchRecordById("r1")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorizedSpy).toHaveBeenCalledTimes(1);

    fetchImpl.mockResolvedValue(respond(404, { detail: "Record not found" }));
    await expect(client.fetchRecordById("r1")).rejects.toMatchObject({ status: 404 });
    expect(onUnauthorizedSpy).toHaveBeenCalledTimes(1);
  });
});
