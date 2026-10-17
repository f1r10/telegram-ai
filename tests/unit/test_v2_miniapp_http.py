"""Mini App HTTP never exposes private data without signed Telegram launch headers."""

import asyncio
import http.client
import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

from office_assistant.miniapp.backend import (
    ChatResponse,
    MiniAppBackend,
)
from office_assistant.miniapp.http import MiniAppHTTPServer


class DummyBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def profile(self, raw: str) -> dict[str, str]:
        self.calls.append("me:" + raw)
        return {"display_name": "Sınaq", "role": "employee"}

    async def answer(self, raw: str, *, question: str, scope: str) -> ChatResponse:
        self.calls.append("ask:" + raw + ":" + scope + ":" + question)
        return ChatResponse("Mənbəli cavab", ((1, "sənəd.pdf"),), False)

    async def product(self, raw: str, *, query: str) -> dict[str, object]:
        self.calls.append("product:" + raw + ":" + query)
        return {
            "status": "found",
            "product": {"code": query, "name": "Təsdiqli məhsul"},
            "candidates": [],
        }

    async def sales_report(self, raw: str, *, customer: str, year: int) -> dict[str, object]:
        self.calls.append("sales:" + raw + ":" + customer + ":" + str(year))
        return {
            "customer": customer,
            "year": year,
            "has_records": True,
            "totals": [{"currency": "AZN", "invoice_count": 2, "amount": "40.00"}],
            "notice": "Təsdiqlənmiş fakturalar.",
        }

    async def stock(self, raw: str, *, code: str) -> dict[str, object]:
        self.calls.append("stock:" + raw + ":" + code)
        return {
            "product_code": code,
            "known": False,
            "total": None,
            "locations": [],
            "notice": "Məlum deyil",
        }


@contextmanager
def serve() -> Generator[tuple[int, DummyBackend], None, None]:
    loop = asyncio.new_event_loop()
    running = threading.Thread(target=loop.run_forever, daemon=True)
    running.start()
    backend = DummyBackend()
    server = MiniAppHTTPServer(
        port=0,
        public_origin="https://assistant.example.org",
        backend=cast(MiniAppBackend, backend),
        loop=loop,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (server.server_address[1], backend)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        running.join(timeout=5)
        loop.close()


def call(
    port: int,
    method: str,
    path: str,
    *,
    body: object = None,
    signed: str | None = "pretend-signed-data",
    origin: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
    headers: dict[str, str] = {}
    if signed is not None:
        headers["X-Telegram-Init-Data"] = signed
    if origin is not None:
        headers["Origin"] = origin
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    result = (response.status, dict(response.getheaders()), response.read())
    connection.close()
    return result


def test_html_and_js_are_static_and_database_never_loads_without_auth() -> None:
    with serve() as (port, backend):
        code, headers, body = call(port, "GET", "/", signed=None)
        assert code == 200
        assert b"Bizim baza" in body
        assert b"initDataUnsafe" not in body
        assert headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert "nosniff" == headers["X-Content-Type-Options"]
        code, _, script = call(port, "GET", "/app.js", signed=None)
        assert code == 200
        assert b"X-Telegram-Init-Data" in script
        assert b"textContent" in script
        assert "bot_token" not in script.decode()
        assert backend.calls == []


def test_missing_auth_and_wrong_origin_cannot_reach_backend() -> None:
    with serve() as (port, backend):
        assert call(port, "GET", "/api/me", signed=None)[0] == 401
        assert call(port, "GET", "/api/me", origin="https://attacker.example")[0] == 403
        assert (
            call(
                port,
                "POST",
                "/api/ask",
                body={"question": "KA45", "scope": "baza"},
                origin="https://attacker.example",
            )[0]
            == 403
        )
        assert backend.calls == []


def test_actual_api_contract_for_chat_and_known_unknown_stock() -> None:
    with serve() as (port, backend):
        code, _, body = call(port, "GET", "/api/me", origin="https://assistant.example.org")
        assert code == 200
        assert json.loads(body)["role"] == "employee"
        code, _, body = call(
            port,
            "POST",
            "/api/ask",
            body={"question": "KA45?", "scope": "baza"},
            origin="https://assistant.example.org",
        )
        assert code == 200
        result = json.loads(body)
        assert result["answer"] == "Mənbəli cavab"
        assert result["sources"] == [{"number": 1, "label": "sənəd.pdf"}]
        code, _, body = call(
            port,
            "POST",
            "/api/stock",
            body={"code": "KA45"},
            origin="https://assistant.example.org",
        )
        assert code == 200
        assert json.loads(body)["known"] is False
        assert len(backend.calls) == 3


def test_malformed_json_and_path_guessing_fail_closed() -> None:
    with serve() as (port, backend):
        assert call(port, "GET", "/?initData=secret", signed=None)[0] == 400
        assert call(port, "GET", "/secrets/.env", signed=None)[0] == 404
        assert call(port, "POST", "/api/ask", body=["not", "an", "object"])[0] == 400
        assert call(port, "POST", "/api/ask", body={"q": "hey"})[0] == 400
        assert backend.calls == []


def test_product_and_sales_http_cards_preserve_permissions_and_currency_values() -> None:
    with serve() as (port, backend):
        code, _, body = call(
            port,
            "POST",
            "/api/product",
            body={"query": "KA45"},
            origin="https://assistant.example.org",
        )
        assert code == 200
        result = json.loads(body)
        assert result["product"]["code"] == "KA45"
        assert "price" not in result["product"]

        code, _, body = call(
            port,
            "POST",
            "/api/sales",
            body={"customer": "A şirkəti", "year": 2026},
            origin="https://assistant.example.org",
        )
        assert code == 200
        result = json.loads(body)
        assert result["totals"] == [{"currency": "AZN", "invoice_count": 2, "amount": "40.00"}]
        assert backend.calls == [
            "product:pretend-signed-data:KA45",
            "sales:pretend-signed-data:A şirkəti:2026",
        ]


def test_product_sales_invalid_payloads_and_origins_fail_without_dispatch() -> None:
    with serve() as (port, backend):
        assert call(port, "POST", "/api/product", body={"query": 123})[0] == 400
        assert call(port, "POST", "/api/sales", body={"customer": "A", "year": True})[0] == 400
        assert call(port, "POST", "/api/sales", body={"customer": "A", "year": "2026"})[0] == 400
        assert call(port, "POST", "/api/product", body={"query": "KA45"}, signed=None)[0] == 401
        assert (
            call(
                port,
                "POST",
                "/api/sales",
                body={"customer": "A", "year": 2026},
                origin="https://evil.example",
            )[0]
            == 403
        )
        assert backend.calls == []
