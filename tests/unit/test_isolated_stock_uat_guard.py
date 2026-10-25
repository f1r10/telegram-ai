"""Only exact disposable DB and synthetic stock can be targeted by acceptance runner."""

from office_assistant.application.inventory_xlsx import preview_stock_xlsx
from office_assistant.core.settings import Settings
from office_assistant.scripts.verify_isolated_stock_uat import (
    DB_NAME,
    HOST_PREFIX,
    PRODUCT_CODE,
    safe_target,
    workbook_bytes,
)


def _settings(host: str, db: str, *, polling: bool = False, env: str = "test") -> Settings:
    return Settings.model_construct(
        app_env=env,
        postgres_db=db,
        postgres_host=host,
        telegram_polling_enabled=polling,
    )


def test_refuses_live_and_untrusted_database_targets() -> None:
    assert not safe_target(_settings("postgres", DB_NAME))
    assert not safe_target(_settings("tgstage_postgres", DB_NAME))
    assert not safe_target(_settings(HOST_PREFIX + "real", "office_assistant"))
    assert not safe_target(_settings(HOST_PREFIX + "real", DB_NAME, polling=True))
    assert not safe_target(_settings(HOST_PREFIX + "real", DB_NAME, env="production"))
    assert safe_target(_settings(HOST_PREFIX + "temporary", DB_NAME))


def test_synthetic_excel_has_only_exact_approved_template() -> None:
    preview = preview_stock_xlsx(workbook_bytes())
    assert preview.count == 2
    assert [draft.product_code for draft in preview.drafts] == [PRODUCT_CODE, PRODUCT_CODE]
    assert [draft.unit for draft in preview.drafts] == ["PCS", "PCS"]
    assert preview.receipt_count == 1
    assert preview.issue_count == 1
    assert preview.opening_count == 0
