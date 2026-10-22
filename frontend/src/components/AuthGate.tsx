import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";

import { authApi, onUnauthorized, type AuthApi, type LoginResult } from "../services/auth";

const LOGIN_ERRORS: Record<Exclude<LoginResult, "ok">, string> = {
  invalid: "הסיסמה שגויה",
  rate_limited: "יותר מדי ניסיונות. נסו שוב בעוד כמה דקות.",
  error: "לא ניתן להתחבר לשרת. נסו שוב.",
};

export interface LoginScreenProps {
  submitting: boolean;
  error: string | null;
  onSubmit(password: string): void;
}

export function LoginScreen({ submitting, error, onSubmit }: LoginScreenProps) {
  const [password, setPassword] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (password) onSubmit(password);
  };
  return (
    <main className="auth-screen">
      <form className="auth-card" onSubmit={submit} aria-label="כניסה">
        <h1 className="auth-title"><img className="auth-logo" src="/logo.png" alt="YMCL" /></h1>
        <label className="auth-label" htmlFor="auth-password">סיסמה</label>
        <input
          id="auth-password"
          className="auth-input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={submitting}
          autoFocus
          required
        />
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button className="auth-submit" type="submit" disabled={submitting || !password}>
          {submitting ? "מתחבר…" : "כניסה"}
        </button>
      </form>
    </main>
  );
}

type GateState =
  | { status: "checking" }
  | { status: "unavailable" }
  | { status: "login"; submitting: boolean; error: string | null }
  | { status: "ready"; authEnabled: boolean };

/** Shows the login screen until the server confirms a session; renders children afterwards. */
export function AuthGate({ children, auth = authApi }: { children: ReactNode; auth?: AuthApi }) {
  const [state, setState] = useState<GateState>({ status: "checking" });

  const checkSession = useCallback(async () => {
    setState({ status: "checking" });
    try {
      const session = await auth.getSession();
      setState(
        session.authenticated
          ? { status: "ready", authEnabled: session.authEnabled }
          : { status: "login", submitting: false, error: null },
      );
    } catch {
      setState({ status: "unavailable" });
    }
  }, [auth]);

  useEffect(() => {
    void checkSession();
  }, [checkSession]);

  useEffect(
    () =>
      onUnauthorized(() =>
        setState((current) =>
          current.status === "ready" && current.authEnabled
            ? { status: "login", submitting: false, error: null }
            : current,
        ),
      ),
    [],
  );

  const login = async (password: string) => {
    setState({ status: "login", submitting: true, error: null });
    const result = await auth.login(password);
    if (result === "ok") {
      setState({ status: "ready", authEnabled: true });
    } else {
      setState({ status: "login", submitting: false, error: LOGIN_ERRORS[result] });
    }
  };

  const logout = async () => {
    try {
      await auth.logout();
    } finally {
      setState({ status: "login", submitting: false, error: null });
    }
  };

  switch (state.status) {
    case "checking":
      return <main className="auth-screen"><p className="auth-status">טוען…</p></main>;
    case "unavailable":
      return (
        <main className="auth-screen">
          <div className="auth-card">
            <p className="auth-error" role="alert">השרת אינו זמין כרגע.</p>
            <button className="auth-submit" type="button" onClick={() => void checkSession()}>נסו שוב</button>
          </div>
        </main>
      );
    case "login":
      return <LoginScreen submitting={state.submitting} error={state.error} onSubmit={(password) => void login(password)} />;
    case "ready":
      return (
        <>
          {children}
          {state.authEnabled && (
            <button className="logout-button" type="button" onClick={() => void logout()}>התנתקות</button>
          )}
        </>
      );
  }
}
