"""Image OCR diagnostic must reveal only controlled booleans, never uploaded text."""

from office_assistant.scripts.diagnose_image_ocr import expected_text_flags


def test_expected_synthetic_image_facts_without_raw_content() -> None:
    content = (
        "Məhsul kodu: QA-IMG-682\n"
        "Qutu miqdarı: 8 ədəd\n"
        "Partiya kodu: L-731\n"
        "Private supplier: SHOULD_NOT_APPEAR\n"
    )
    flags = expected_text_flags(content, code="QA-IMG-682", quantity=8, lot="L-731")
    assert flags["ocr_code_exact"] is True
    assert flags["ocr_quantity_present"] is True
    assert flags["ocr_lot_exact"] is True
    assert flags["ocr_policy_identifier_overlap"] is True
    assert "SHOULD_NOT_APPEAR" not in repr(flags)
    assert content not in repr(flags)


def test_code_mismatch_and_nearby_quantity_fail_closed() -> None:
    content = "Məhsul QA-IMG-683, qutu miqdarı 18 ədəd, lot L-732"
    flags = expected_text_flags(content, code="QA-IMG-682", quantity=8, lot="L-731")
    assert flags["ocr_code_exact"] is False
    assert flags["ocr_quantity_present"] is False
    assert flags["ocr_lot_exact"] is False
    assert flags["ocr_policy_identifier_overlap"] is False


def test_unrequested_quantities_and_lots_stay_unreported() -> None:
    flags = expected_text_flags(
        "QA-PDF-318 zəmanət müddəti 33 ay",
        code="QA-PDF-318",
        quantity=None,
        lot=None,
    )
    assert flags["ocr_code_exact"] is True
    assert flags["ocr_quantity_present"] is None
    assert flags["ocr_lot_exact"] is None
