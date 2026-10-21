"""Non-mutating localhost Mini App smoke: assets, headers and unsigned-request denial.

Run on the host with a staged, explicitly enabled Mini App. No credentials,
company data, Telegram requests or stock writes are used by this probe.
"""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass

import httpx


@dataclass(frozen=True, slots=True)
class SmokeCheck:
    name: str
    passed: bool


async def probe(port: int = 18791) -> tuple[bool, list[SmokeCheck]]:
    if not 1024 <= port <= 65535:
        raise ValueError("Only an unprivileged localhost port is supported")
    checks: list[SmokeCheck] = []
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        timeout=httpx.Timeout(5.0),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        for name, path, mime in (
            ("static_html", "/", "text/html"),
            ("static_js", "/app.js", "text/javascript"),
            ("static_css", "/app.css", "text/css"),
        ):
            try:
                response = await client.get(path)
                valid = (
                    response.status_code == 200
                    and response.headers.get("Content-Type", "").startswith(mime)
                    and response.headers.get("Cache-Control") == "no-store"
                    and response.headers.get("X-Content-Type-Options") == "nosniff"
                    and response.headers.get("Referrer-Policy") == "no-referrer"
                    and "default-src 'none'"
                    in response.headers.get("Content-Security-Policy", "")
                    and "Set-Cookie" not in response.headers
                    and len(response.content) > 0
                )
            except (httpx.HTTPError, ValueError):
                valid = False
            checks.append(SmokeCheck(name, valid))
        try:
            response = await client.get("/api/me")
            denied = response.status_code == 401
        except httpx.HTTPError:
            denied = False
        checks.append(SmokeCheck("unsigned_profile_denied", denied))
        try:
            response = await client.post(
                "/api/stock/transfer/confirm",
                json={"token": "invalid-local-smoke-ticket"},
            )
            denied = response.status_code == 401
        except httpx.HTTPError:
            denied = False
        checks.append(SmokeCheck("unsigned_stock_write_denied", denied))
    return all(item.passed for item in checks), checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18791)
    args = parser.parse_args()
    try:
        passed, checks = asyncio.run(probe(args.port))
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": type(error).__name__}))
        raise SystemExit(2) from None
    print(
        json.dumps(
            {
                "status": "SMOKE_PASS" if passed else "SMOKE_BLOCKED",
                "checks": [asdict(item) for item in checks],
                "telegram_real_device_verified": False,
                "production_accepted": False,
            }
        )
    )
    raise SystemExit(0 if passed else 2)


if __name__ == "__main__":
    main()
