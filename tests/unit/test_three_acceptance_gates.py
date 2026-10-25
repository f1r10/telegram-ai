"""Acceptance preflight never treats configuration or counts as live acceptance."""

import json
from typing import cast

from pydantic import SecretStr

from office_assistant.core.settings import Settings
from office_assistant.scripts.check_three_acceptance_gates import GateCounts, build_report


def counts(*, photos: int = 0) -> GateCounts:
    return GateCounts(2, 3, 4, 1, photos)


def test_no_mobile_photo_or_live_stock_approval_is_inferred_from_counts() -> None:
    result = build_report(Settings(_env_file=None), counts(photos=30))
    miniapp = cast(dict[str, object], result["miniapp"])
    inventory = cast(dict[str, object], result["inventory"])
    photo = cast(dict[str, object], result["photo_recognition"])

    assert result["status"] == "PREFLIGHT_ONLY"
    assert miniapp["gate"] == "NOT_ENABLED_FOR_DEVICE_TEST"
    assert miniapp["real_telegram_mobile_accepted"] is False
    assert inventory["gate"] == "ISOLATED_OPERATOR_UAT_REQUIRED"
    assert inventory["known_stock_balances"] == 4
    assert inventory["live_mutation_accepted"] is False
    assert photo["active_confirmed_reference_relations"] == 30
    assert photo["real_holdout_benchmark_accepted"] is False
    assert photo["gate"] == "REAL_LABELLED_PHOTOS_REQUIRED"


def test_configured_origin_and_flags_are_not_mistaken_for_device_uat() -> None:
    settings = Settings(
        _env_file=None,
        miniapp_enabled=True,
        miniapp_public_origin="https://office.private.example",
        miniapp_stock_transfer_enabled=True,
        miniapp_stock_actions_enabled=True,
        miniapp_stock_xlsx_enabled=True,
        postgres_password=SecretStr("top-secret-dummy"),
    )
    result = build_report(settings, counts())
    miniapp = cast(dict[str, object], result["miniapp"])
    assert miniapp["gate"] == "DEVICE_HTTPS_UAT_REQUIRED"
    assert miniapp["real_telegram_mobile_accepted"] is False
    assert miniapp["stock_transfer_enabled"] is True

    rendered = json.dumps(result)
    assert "top-secret-dummy" not in rendered
    assert "office.private.example" not in rendered


def test_http_origin_never_qualifies_for_mobile_gate() -> None:
    settings = Settings(
        _env_file=None,
        miniapp_enabled=True,
        miniapp_public_origin="http://localhost:18791",
    )
    miniapp = cast(dict[str, object], build_report(settings, counts())["miniapp"])
    assert miniapp["https_origin_configured"] is False
    assert miniapp["gate"] == "NOT_ENABLED_FOR_DEVICE_TEST"
