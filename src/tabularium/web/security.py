"""Protection for a server on 127.0.0.1 (see docs/adr/0007).

- Host allow-list defeats DNS rebinding.
- State-changing requests need a matching Origin (when sent) and the per-process CSRF token
  in the ``X-CSRF-Token`` header.
"""

import secrets
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _split_host(host_header: str) -> tuple[str, str | None]:
    if host_header.startswith("["):  # IPv6 literal
        end = host_header.find("]")
        return host_header[: end + 1], host_header[end + 2 :] or None
    name, _, port = host_header.partition(":")
    return name, port or None


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, csrf_token: str, allowed_hosts: frozenset[str] = ALLOWED_HOSTS):
        super().__init__(app)
        self.csrf_token = csrf_token
        self.allowed_hosts = allowed_hosts

    async def dispatch(self, request: Request, call_next) -> Response:
        host_header = request.headers.get("host", "")
        host, _port = _split_host(host_header.lower())
        if host not in self.allowed_hosts:
            return PlainTextResponse("Invalid host", status_code=400)

        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and urlsplit(origin).netloc.lower() != host_header.lower():
                return PlainTextResponse("Cross-origin request rejected", status_code=403)
            token = request.headers.get(CSRF_HEADER, "")
            if not secrets.compare_digest(token, self.csrf_token):
                return PlainTextResponse("Missing or invalid CSRF token", status_code=403)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response
