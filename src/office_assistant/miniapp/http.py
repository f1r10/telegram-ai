"""Loopback-only HTTP transport for Mini App, behind an operator-managed TLS proxy.

No external HTTP framework dependency and no public listening port. The web
transport is separate from the Telegram polling application; an outage cannot
disable the bot. All /api requests are signed and database-authorized.
"""

import asyncio
import json
import logging
import re
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from collections.abc import Coroutine
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from socket import socket
from typing import Any, cast
from urllib.parse import urlsplit

from office_assistant.application.inventory import InventoryValidationError
from office_assistant.miniapp.auth import MiniAppAuthenticationError
from office_assistant.miniapp.backend import (
    MiniAppAccessDenied,
    MiniAppBackend,
    MiniAppRateLimited,
    MiniAppServiceUnavailable,
)

logger = logging.getLogger(__name__)
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
_CSP = (
    "default-src 'none'; script-src 'self' https://telegram.org; "
    "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; object-src 'none'; "
    "frame-ancestors https://web.telegram.org https://*.telegram.org"
)
_MAX_POST_BODY = 4096


class MiniAppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        *,
        port: int,
        public_origin: str,
        backend: MiniAppBackend,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        origin = urlsplit(public_origin)
        if (
            origin.scheme != "https"
            or not origin.netloc
            or origin.username is not None
            or origin.password is not None
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ValueError("Mini App requires a canonical HTTPS public origin")
        if port < 1024 or port > 65535:
            raise ValueError("Choose an unprivileged loopback listen port")
        self.public_origin = public_origin.rstrip("/")
        self.backend = backend
        self.loop = loop
        self.limiter = threading.BoundedSemaphore(16)
        super().__init__(("127.0.0.1", port), MiniAppHandler)

    def process_request(self, request: socket, client_address: tuple[str, int]) -> None:
        if not self.limiter.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.limiter.release()
            raise

    def process_request_thread(self, request: socket, client_address: tuple[str, int]) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.limiter.release()


class MiniAppHandler(BaseHTTPRequestHandler):
    server: MiniAppHTTPServer
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: object) -> None:
        # Do NOT log auth headers, bodies, Telegram query IDs, or company questions.
        pass

    def _send(self, code: int, body: bytes, mime: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, payload: dict[str, object]) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _allowed_origin(self) -> bool:
        headers = self.headers.get_all("Origin", [])
        return len(headers) <= 1 and (
            not headers or headers[0] == self.server.public_origin
        )

    def _authenticate_header(self) -> str | None:
        values = self.headers.get_all("X-Telegram-Init-Data", [])
        if len(values) != 1 or not values[0] or len(values[0]) > 8192:
            return None
        return values[0]

    def _run(self, coroutine: Coroutine[Any, Any, object]) -> object:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.server.loop)
        try:
            return future.result(timeout=80)
        except FutureTimeoutError:
            future.cancel()
            raise MiniAppServiceUnavailable("Operation timed out") from None

    def _process_api(self, method: str, path: str, args: dict[str, object] | None) -> None:
        if not self._allowed_origin():
            self._json(403, {"error": "origin_not_allowed"})
            return
        signed = self._authenticate_header()
        if signed is None:
            self._json(401, {"error": "telegram_authorization_required"})
            return
        try:
            if method == "GET" and path == "/api/me":
                value = self._run(self.server.backend.profile(signed))
                assert isinstance(value, dict)
                self._json(200, cast(dict[str, object], value))
            elif method == "POST" and path == "/api/ask" and args is not None:
                question = args.get("question")
                scope = args.get("scope")
                if not isinstance(question, str) or not isinstance(scope, str):
                    raise ValueError("Invalid question")
                answer = self._run(
                    self.server.backend.answer(signed, question=question, scope=scope)
                )
                from office_assistant.miniapp.backend import ChatResponse

                assert isinstance(answer, ChatResponse)
                self._json(
                    200,
                    {
                        "answer": answer.answer,
                        "sources": [
                            {"number": n, "label": label} for n, label in answer.sources
                        ],
                        "insufficient_evidence": answer.insufficient_evidence,
                    },
                )
            elif method == "POST" and path == "/api/stock" and args is not None:
                code = args.get("code")
                if not isinstance(code, str):
                    raise ValueError("Invalid code")
                value = self._run(self.server.backend.stock(signed, code=code))
                assert isinstance(value, dict)
                self._json(200, cast(dict[str, object], value))
            else:
                self._json(404, {"error": "not_found"})
        except (MiniAppAuthenticationError, MiniAppAccessDenied):
            self._json(401, {"error": "unauthorized"})
        except (ValueError, InventoryValidationError):
            self._json(400, {"error": "invalid_request"})
        except MiniAppRateLimited:
            self._json(429, {"error": "too_many_requests"})
        except MiniAppServiceUnavailable:
            self._json(503, {"error": "service_unavailable"})
        except Exception as error:
            logger.warning("Mini App API failed | error_type=%s", type(error).__name__)
            self._json(503, {"error": "service_unavailable"})

    def do_GET(self) -> None:
        path = urlsplit(self.path)
        if path.query or path.fragment:
            self._json(400, {"error": "no_query_parameters"})
            return
        if path.path == "/api/me":
            self._process_api("GET", path.path, None)
            return
        resource = _STATIC.get(path.path)
        if resource is None:
            self._json(404, {"error": "not_found"})
            return
        filename, mime = resource
        contents = (
            files("office_assistant.miniapp").joinpath("assets").joinpath(filename).read_bytes()
        )
        self._send(200, contents, mime)

    def do_POST(self) -> None:
        path = urlsplit(self.path)
        if path.path not in {"/api/ask", "/api/stock"} or path.query:
            self._json(404, {"error": "not_found"})
            return
        headers = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(headers) != 1:
            self._json(400, {"error": "invalid_request"})
            return
        if not re.fullmatch(r"[0-9]{1,5}", headers[0]):
            self._json(400, {"error": "invalid_request"})
            return
        length = int(headers[0])
        if length <= 0 or length > _MAX_POST_BODY:
            self._json(413, {"error": "request_too_large"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            self._json(415, {"error": "json_required"})
            return
        try:
            parsed: object = json.loads(self.rfile.read(length))
            if not isinstance(parsed, dict):
                raise ValueError("Invalid input object")
            args = cast(dict[str, object], parsed)
            if len(args) > 5:
                raise ValueError("Too many fields")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid_request"})
            return
        self._process_api("POST", path.path, args)
