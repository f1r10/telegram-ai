"""Staging smoke scripts remain transient, opt-in and isolated from public ports."""

from pathlib import Path


def test_disposable_stock_launcher_never_targets_live_postgres() -> None:
    script = Path("scripts/run_isolated_stock_uat.sh").read_text(encoding="utf-8")
    assert "--tmpfs /var/lib/postgresql/data" in script
    assert "tgstage_stock_drill_" in script
    assert "POSTGRES_HOST=$container" in script
    assert "--no-deps" in script
    assert "TELEGRAM_POLLING_ENABLED=false" in script
    assert "docker rm -f" in script
    assert "down -v" not in script
    assert "docker volume rm" not in script


def test_miniapp_smoke_is_never_published_or_inventory_enabled() -> None:
    script = Path("scripts/run_miniapp_local_smoke.sh").read_text(encoding="utf-8")
    assert "--no-deps" in script
    assert "MINIAPP_CONTAINER_BIND=false" in script
    assert "MINIAPP_STOCK_TRANSFER_ENABLED=false" in script
    assert "MINIAPP_STOCK_ACTIONS_ENABLED=false" in script
    assert "MINIAPP_STOCK_XLSX_ENABLED=false" in script
    assert "--service-ports" not in script
    assert "127.0.0.1:" not in script
    assert "MINIAPP_LOCAL_SMOKE=PASS" in script
    assert "TELEGRAM_REAL_MOBILE_UAT=NOT_TESTED" in script
