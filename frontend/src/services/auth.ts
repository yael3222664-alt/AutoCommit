/** Session login against /api/auth/*; the server keeps the session in an HttpOnly cookie. */

export interface SessionState {
  authEnabled: boolean;
  authenticated: boolean;
}

export type LoginResult = "ok" | "invalid" | "rate_limited" | "error";

export interface AuthApi {
  getSession(): Promise<SessionState>;
  login(password: string): Promise<LoginResult>;
  logout(): Promise<void>;
}

export interface AuthApiOptions {
  baseUrl?: string;
  fetchImpl?: (input: string, init?: RequestInit) => Promise<Response>;
}

export function createAuthApi(options: AuthApiOptions = {}): AuthApi {
  const baseUrl = (options.baseUrl ?? "").replace(/\/+$/, "");
  const fetchImpl = options.fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
  const url = (path: string) => `${baseUrl}/api/auth${path}`;

  return {
    async getSession() {
      const response = await fetchImpl(url("/session"), { method: "GET", credentials: "same-origin" });
      if (!response.ok) throw new Error(`Session check failed with status ${response.status}`);
      const body: unknown = await response.json();
      if (
        typeof body !== "object" ||
        body === null ||
        typeof (body as Record<string, unknown>).auth_enabled !== "boolean" ||
        typeof (body as Record<string, unknown>).authenticated !== "boolean"
      ) {
        throw new Error("Unexpected session response");
      }
      const session = body as { auth_enabled: boolean; authenticated: boolean };
      return { authEnabled: session.auth_enabled, authenticated: session.authenticated };
    },

    async login(password) {
      let response: Response;
      try {
        response = await fetchImpl(url("/login"), {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password }),
        });
      } catch {
        return "error";
      }
      if (response.ok) return "ok";
      if (response.status === 401) return "invalid";
      if (response.status === 429) return "rate_limited";
      return "error";
    },

    async logout() {
      await fetchImpl(url("/logout"), { method: "POST", credentials: "same-origin" });
    },
  };
}

const unauthorizedListeners = new Set<() => void>();

/** Subscribe to "the server rejected our session"; returns an unsubscribe function. */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export function notifyUnauthorized(): void {
  for (const listener of [...unauthorizedListeners]) listener();
}

export const authApi: AuthApi = createAuthApi({ baseUrl: import.meta.env.VITE_API_BASE_URL ?? "" });
