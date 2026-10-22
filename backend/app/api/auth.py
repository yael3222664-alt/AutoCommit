"""Shared-password session login guarding the API.

Sessions are stateless HMAC-signed cookies: rotating SESSION_SECRET signs everyone out.
"""
import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import Settings

SESSION_COOKIE = "autocommit_session"
PUBLIC_API_PATHS = frozenset({"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/session"})
_MAX_TRACKED_CLIENTS = 10_000

Clock = Callable[[], float]


class SessionSigner:
    def __init__(self, secret: bytes, max_age_seconds: int, clock: Clock = time.time) -> None:
        self._secret = secret
        self._max_age = max_age_seconds
        self._clock = clock

    def issue(self) -> str:
        expires = int(self._clock()) + self._max_age
        payload = f"{expires}.{secrets.token_urlsafe(16)}"
        return f"{payload}.{self._sign(payload)}"

    def verify(self, token: Optional[str]) -> bool:
        if not token:
            return False
        payload, separator, signature = token.rpartition(".")
        if not separator or not hmac.compare_digest(signature.encode(), self._sign(payload).encode()):
            return False
        try:
            expires = int(payload.split(".", 1)[0])
        except ValueError:
            return False
        return expires > self._clock()

    def _sign(self, payload: str) -> str:
        digest = hmac.new(self._secret, payload.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class LoginRateLimiter:
    """Blocks a client after too many failed logins inside a sliding window."""

    def __init__(self, max_failures: int, window_seconds: float, clock: Clock = time.monotonic) -> None:
        self._max_failures = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, client: str) -> int:
        """Seconds until the client may try again; 0 when not blocked."""
        with self._lock:
            attempts = self._recent(client)
            if len(attempts) < self._max_failures:
                return 0
            return max(1, int(attempts[0] + self._window - self._clock()) + 1)

    def record_failure(self, client: str) -> None:
        with self._lock:
            if client not in self._failures and len(self._failures) >= _MAX_TRACKED_CLIENTS:
                self._failures.pop(next(iter(self._failures)))
            self._recent(client).append(self._clock())

    def reset(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def _recent(self, client: str) -> deque[float]:
        attempts = self._failures.setdefault(client, deque())
        cutoff = self._clock() - self._window
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return attempts


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    password: Optional[SecretStr] = None
    signer: Optional[SessionSigner] = None
    limiter: Optional[LoginRateLimiter] = None
    max_age_seconds: int = 0
    secure_cookie: bool = True

    def is_authenticated(self, request: Request) -> bool:
        if not self.enabled:
            return True
        return self.signer is not None and self.signer.verify(request.cookies.get(SESSION_COOKIE))

    def password_matches(self, candidate: str) -> bool:
        if self.password is None:
            return False
        expected = hashlib.sha256(self.password.get_secret_value().encode()).digest()
        return hmac.compare_digest(hashlib.sha256(candidate.encode()).digest(), expected)


def build_auth_config(settings: Settings) -> AuthConfig:
    if not settings.AUTH_ENABLED:
        return AuthConfig(enabled=False)
    if settings.APP_ACCESS_PASSWORD is None or settings.SESSION_SECRET is None:
        raise ValueError("AUTH_ENABLED requires APP_ACCESS_PASSWORD and SESSION_SECRET")
    return AuthConfig(
        enabled=True,
        password=settings.APP_ACCESS_PASSWORD,
        signer=SessionSigner(settings.SESSION_SECRET.get_secret_value().encode(), settings.SESSION_MAX_AGE_SECONDS),
        limiter=LoginRateLimiter(settings.LOGIN_MAX_FAILURES, settings.LOGIN_FAILURE_WINDOW_SECONDS),
        max_age_seconds=settings.SESSION_MAX_AGE_SECONDS,
        secure_cookie=settings.ENVIRONMENT == "production",
    )


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class SessionResponse(BaseModel):
    auth_enabled: bool
    authenticated: bool


def build_auth_router(config: AuthConfig) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.get("/session", response_model=SessionResponse)
    def session(request: Request) -> SessionResponse:
        return SessionResponse(auth_enabled=config.enabled, authenticated=config.is_authenticated(request))

    @router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
    def login(body: LoginRequest, request: Request) -> Response:
        if not config.enabled or config.signer is None or config.limiter is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Login is not enabled")
        client = request.client.host if request.client else "unknown"
        retry_after = config.limiter.retry_after(client)
        if retry_after:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many failed attempts; try again later",
                headers={"Retry-After": str(retry_after)},
            )
        if not config.password_matches(body.password):
            config.limiter.record_failure(client)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect password")
        config.limiter.reset(client)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.set_cookie(
            SESSION_COOKIE,
            config.signer.issue(),
            max_age=config.max_age_seconds,
            httponly=True,
            secure=config.secure_cookie,
            samesite="strict",
            path="/",
        )
        return response

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout() -> Response:
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=config.secure_cookie, samesite="strict")
        return response

    return router


class AuthGuardMiddleware:
    """Rejects unauthenticated requests to every /api route except PUBLIC_API_PATHS."""

    def __init__(self, app: ASGIApp, config: AuthConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.config.enabled:
            path = scope["path"]
            if (path == "/api" or path.startswith("/api/")) and path not in PUBLIC_API_PATHS:
                if not self.config.is_authenticated(Request(scope)):
                    response = JSONResponse({"detail": "Authentication required"}, status_code=401)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
