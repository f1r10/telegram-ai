"""Mini App HTTP never exposes private data without signed Telegram launch headers."""

import asyncio
import http.client
import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from urllib.parse import quote

from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.internal_evidence.delivery import DeliverableFile, FileDeliveryError
from office_assistant.miniapp.backend import (
    ChatResponse,
    MiniAppBackend,
)
from office_assistant.miniapp.http import MiniAppHTTPServer
from office_assistant.miniapp.uploads import MiniAppUploadWorkflow


class DummyBackend:
    def __init__(self, original: Path | None = None, upload_root: Path | None = None) -> None:
        self.calls: list[str] = []
        self.original = original
        self.uploads = (
            cast(MiniAppUploadWorkflow, SimpleNamespace(storage=LocalFileStorage(upload_root)))
            if upload_root is not None
            else None
        )

    async def recent_uploads(self, raw: str) -> dict[str, object]:
        self.calls.append("jobs:" + raw)
        return {
            "jobs": [
                {
                    "job_id": 77,
                    "filename": "contract.pdf",
                    "stage": "indexing",
                    "notice": "Axtarış indeksinə əlavə olunur.",
                    "terminal": False,
                }
            ]
        }

    async def upload_status(self, raw: str, *, job_id: int) -> dict[str, object]:
        self.calls.append("job:" + raw + ":" + str(job_id))
        if job_id != 77:
            from office_assistant.miniapp.progress import UploadJobNotFound

            raise UploadJobNotFound("Not owned")
        return {
            "job_id": job_id,
            "filename": "contract.pdf",
            "stage": "ready",
            "notice": "Sənəd axtarış üçün hazırdır.",
            "terminal": True,
        }

    async def profile(self, raw: str) -> dict[str, str]:
        self.calls.append("me:" + raw)
        return {"display_name": "Sınaq", "role": "employee"}

    async def answer(self, raw: str, *, question: str, scope: str) -> ChatResponse:
        self.calls.append("ask:" + raw + ":" + scope + ":" + question)
        return ChatResponse("Mənbəli cavab", ((1, "sənəd.pdf"),), False)

    async def actor(self, raw: str) -> object:
        self.calls.append("actor:" + raw)
        return object()

    async def photo(self, raw: str, *, image_bytes: bytes) -> dict[str, object]:
        self.calls.append("photo:" + raw + ":" + str(len(image_bytes)))
        return {
            "status": "candidates",
            "codes": ["KA45"],
            "method": "cross_angle",
            "incomplete": False,
            "notice": "Məhsul kodunu yoxlayın.",
        }

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

    async def upload_permission(self, raw: str) -> None:
        self.calls.append("upload-permission:" + raw)

    async def register_upload(
        self, raw: str, *, key: str, filename: str, mime: str, size: int
    ) -> dict[str, object]:
        self.calls.append("upload-register:" + raw + ":" + filename)
        assert self.uploads is not None
        assert self.uploads.storage.size(self.uploads.storage.path_for_key(key)) == size
        return {
            "token": "uploaded-preview-ticket-1234567890",
            "filename": filename,
            "size": size,
            "status": "preview",
        }

    async def confirm_upload(self, raw: str, *, token: str) -> dict[str, object]:
        self.calls.append("upload-confirm:" + raw + ":" + token)
        return {"status": "queued", "job_id": 77, "notice": "Saved for indexing"}

    async def cancel_upload(self, raw: str, *, token: str) -> dict[str, object]:
        self.calls.append("upload-cancel:" + raw + ":" + token)
        return {"status": "cancelled", "notice": "Not saved"}

    async def source_file(self, raw: str, *, token: str) -> DeliverableFile:
        self.calls.append("source:" + raw + ":" + token)
        if self.original is None:
            raise FileDeliveryError("No file selected")
        return DeliverableFile(self.original, "sənəd nümunəsi.pdf", "application/pdf", 9)

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
def serve(
    original: Path | None = None, upload_root: Path | None = None
) -> Generator[tuple[int, DummyBackend], None, None]:
    loop = asyncio.new_event_loop()
    running = threading.Thread(target=loop.run_forever, daemon=True)
    running.start()
    backend = DummyBackend(original, upload_root)
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
        assert result["sources"] == [{"number": 1, "label": "sənəd.pdf", "download_token": None}]
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


def test_source_is_downloaded_by_private_authenticated_post_only(tmp_path: Path) -> None:
    original = tmp_path / "original.pdf"
    bytes_value = b"%PDF-original-binary-test"
    original.write_bytes(bytes_value)
    with serve(original) as (port, backend):
        response, headers, body = call(port, "POST", "/api/source", body={"token": "opaque-test"})
        assert response == 200
        assert body == bytes_value
        assert headers["Content-Type"] == "application/octet-stream"
        assert headers["Cache-Control"] == "private, no-store"
        assert "attachment;" in headers["Content-Disposition"]
        assert "s%C9%99n%C9%99d" in headers["Content-Disposition"]
        assert backend.calls == ["source:pretend-signed-data:opaque-test"]


def test_source_routes_reject_unsigned_and_forged_file_requests(tmp_path: Path) -> None:
    original = tmp_path / "original.pdf"
    original.write_bytes(b"fixture")
    with serve(original) as (port, backend):
        assert call(port, "GET", "/api/source")[0] == 404
        assert call(port, "POST", "/api/source", body={"token": "any"}, signed=None)[0] == 401
        assert call(port, "POST", "/api/source", body={"token": 123})[0] == 400
        assert (
            call(
                port,
                "POST",
                "/api/source",
                body={"token": "source-token"},
                origin="https://malicious.example",
            )[0]
            == 403
        )
        assert backend.calls == []


def send_binary(
    port: int,
    filename: str,
    content: bytes,
    *,
    signed: str | None = "pretend-signed-data",
    origin: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    headers = {
        "X-Office-Filename": quote(filename),
        "Content-Type": "application/pdf",
    }
    if signed is not None:
        headers["X-Telegram-Init-Data"] = signed
    if origin is not None:
        headers["Origin"] = origin
    connection.request("POST", "/api/upload/prepare", body=content, headers=headers)
    reply = connection.getresponse()
    output = (reply.status, dict(reply.getheaders()), reply.read())
    connection.close()
    return output


def test_upload_binary_preview_then_explicit_confirm_and_cancel(tmp_path: Path) -> None:
    with serve(upload_root=tmp_path / "staging") as (port, backend):
        status, _, body = send_binary(
            port, "sənəd.pdf", b"%PDF-example", origin="https://assistant.example.org"
        )
        assert status == 200
        preview = json.loads(body)
        assert preview["status"] == "preview"
        assert preview["filename"] == "sənəd.pdf"
        assert "upload-register:pretend-signed-data:sənəd.pdf" in backend.calls
        assert not any(x.startswith("upload-confirm:") for x in backend.calls)
        token = preview["token"]
        status, _, data = call(port, "POST", "/api/upload/confirm", body={"token": token})
        assert status == 200
        assert json.loads(data)["status"] == "queued"
        status, _, data = call(port, "POST", "/api/upload/cancel", body={"token": token})
        assert status == 200
        assert json.loads(data)["status"] == "cancelled"
        assert backend.calls[-2:] == [
            "upload-confirm:pretend-signed-data:" + token,
            "upload-cancel:pretend-signed-data:" + token,
        ]


def test_upload_rejects_unsigned_wrong_origin_unsupported_and_large(tmp_path: Path) -> None:
    with serve(upload_root=tmp_path / "staging") as (port, backend):
        assert send_binary(port, "test.pdf", b"%PDF", signed=None)[0] == 401
        assert send_binary(port, "test.pdf", b"%PDF", origin="https://wrong.example")[0] == 403
        assert send_binary(port, "../test.pdf", b"%PDF")[0] == 400
        assert send_binary(port, "test.exe", b"%PDF")[0] == 400
        # The server rejects the declared oversized body before streaming;
        # transmitting >8 MiB here would race the intentional socket close.
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
        connection.putrequest("POST", "/api/upload/prepare")
        connection.putheader("X-Telegram-Init-Data", "pretend-signed-data")
        connection.putheader("X-Office-Filename", "test.pdf")
        connection.putheader("Content-Type", "application/pdf")
        connection.putheader("Content-Length", str(8 * 1024 * 1024 + 1))
        connection.endheaders()
        result = connection.getresponse()
        assert result.status == 413
        result.read()
        connection.close()
        assert call(port, "POST", "/api/upload/confirm", body={"token": 123})[0] == 400
        assert call(port, "POST", "/api/upload/cancel", body={"token": "x"}, signed=None)[0] == 401
        assert backend.calls == []


def test_own_document_progress_and_missing_job_http_contract() -> None:
    with serve() as (port, backend):
        code, _, body = call(port, "POST", "/api/uploads/recent", body={})
        assert code == 200
        rows = json.loads(body)["jobs"]
        assert rows[0]["stage"] == "indexing"
        assert rows[0]["terminal"] is False

        code, _, body = call(port, "POST", "/api/uploads/status", body={"job_id": 77})
        assert code == 200
        assert json.loads(body)["stage"] == "ready"

        assert call(port, "POST", "/api/uploads/status", body={"job_id": 78})[0] == 404
        assert backend.calls == [
            "jobs:pretend-signed-data",
            "job:pretend-signed-data:77",
            "job:pretend-signed-data:78",
        ]


def test_document_progress_rejects_unsigned_foreign_origins_and_invalid_ids() -> None:
    with serve() as (port, backend):
        assert call(port, "POST", "/api/uploads/recent", body={}, signed=None)[0] == 401
        assert (
            call(
                port,
                "POST",
                "/api/uploads/status",
                body={"job_id": 77},
                origin="https://attacker.example",
            )[0]
            == 403
        )
        assert call(port, "POST", "/api/uploads/status", body={"job_id": "77"})[0] == 400
        assert call(port, "POST", "/api/uploads/status", body={"job_id": True})[0] == 400
        assert call(port, "POST", "/api/uploads/recent", body={"user_id": 123})[0] == 400
        assert backend.calls == []


def send_photo(
    port: int,
    data: bytes,
    *,
    signed: str | None = "pretend-signed-data",
    origin: str | None = None,
    mime: str = "image/png",
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    headers: dict[str, str] = {"Content-Type": mime}
    if signed is not None:
        headers["X-Telegram-Init-Data"] = signed
    if origin is not None:
        headers["Origin"] = origin
    connection.request("POST", "/api/photo/lookup", body=data, headers=headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_signed_binary_photo_returns_candidates_without_uploading() -> None:
    with serve() as (port, backend):
        status, headers, body = send_photo(
            port, b"ephemeral-bytes", origin="https://assistant.example.org"
        )
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        payload = json.loads(body)
        assert payload == {
            "status": "candidates",
            "codes": ["KA45"],
            "method": "cross_angle",
            "incomplete": False,
            "notice": "Məhsul kodunu yoxlayın.",
        }
        assert backend.calls == [
            "actor:pretend-signed-data",
            "photo:pretend-signed-data:15",
        ]


def test_unsigned_wrong_origin_and_invalid_photo_headers_never_dispatch() -> None:
    with serve() as (port, backend):
        assert send_photo(port, b"photo", signed=None)[0] == 401
        assert send_photo(port, b"photo", origin="https://evil.example")[0] == 403
        assert send_photo(port, b"photo", mime="application/octet-stream")[0] == 400
        assert send_photo(port, b"")[0] == 413
        assert call(port, "GET", "/api/photo/lookup")[0] == 404
        assert backend.calls == []


def test_photo_content_length_is_rejected_before_reading_oversized_payload() -> None:
    with serve() as (port, backend):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
        connection.putrequest("POST", "/api/photo/lookup")
        connection.putheader("Content-Type", "image/png")
        connection.putheader("X-Telegram-Init-Data", "pretend-signed-data")
        connection.putheader("Content-Length", str(4 * 1024 * 1024 + 1))
        connection.endheaders()
        result = connection.getresponse()
        assert result.status == 413
        result.read()
        connection.close()
        assert backend.calls == []
