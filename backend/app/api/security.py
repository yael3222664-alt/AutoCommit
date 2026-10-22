"""Security response headers and cross-origin write protection."""
from typing import Iterable
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
_BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
_PRODUCTION_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SecurityMiddleware:
    """Adds security headers; with cookie sessions, rejects state-changing requests from foreign origins."""

    def __init__(
        self,
        app: ASGIApp,
        allowed_origins: Iterable[str] = (),
        production: bool = False,
        check_origin: bool = True,
    ) -> None:
        self.app = app
        self.check_origin = check_origin
        self.allowed_origins = frozenset(origin.rstrip("/") for origin in allowed_origins)
        self.headers = {**_BASE_HEADERS, **(_PRODUCTION_HEADERS if production else {})}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    headers.setdefault(name, value)
            await send(message)

        unsafe = scope["method"] not in _SAFE_METHODS
        if self.check_origin and unsafe and not self._origin_allowed(Headers(scope=scope)):
            response = JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
            await response(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)

    def _origin_allowed(self, headers: Headers) -> bool:
        origin = headers.get("origin")
        if origin is None:
            return True
        origin = origin.rstrip("/")
        return urlsplit(origin).netloc == headers.get("host") or origin in self.allowed_origins
