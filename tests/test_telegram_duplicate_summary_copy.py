"""Distinguish global byte reuse from duplicates within one pending session."""

from office_assistant.integrations.telegram import messages


def test_save_summary_labels_duplicate_scope() -> None:
    rendered = messages.SAVE_SUCCESS.format(
        files=1,
        instructions=0,
        duplicates=0,
    )
    assert "Fayllar: 1" in rendered
    assert "Bu sessiyada təkrarlanan fayllar: 0" in rendered
    assert "Emal statusu: növbədə" in rendered


def test_upload_duplicate_copy_explicitly_marks_reused_content() -> None:
    assert "eyni məzmunu əvvəlcədən mövcuddur" in messages.FILE_DUPLICATE
