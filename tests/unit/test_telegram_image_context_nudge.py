"""Image context is required only for deictic questions without a named source."""

import pytest

from office_assistant.integrations.telegram.turn_context import needs_an_attached_picture


@pytest.mark.parametrize(
    "question",
    [
        "Şəkildəki məhsul kodu, qutu miqdarı və partiya kodu nədir?",
        "Bu foto üzərindəki mətni oxu",
        "Fotodakı etiketdə nə yazılıb?",
        "Bu şəkil hansı məhsulundur?",
    ],
)
def test_implicit_image_queries_require_attachment(question: str) -> None:
    assert needs_an_attached_picture(question)


@pytest.mark.parametrize(
    "question",
    [
        "QA-IMG-682 məhsulunun qutu miqdarı neçə ədəddir?",
        "QA-IMG-682 şəklindəki məhsul kodu nədir?",
        "Bizim bazada PDF sənədi varmı?",
        "Salam",
    ],
)
def test_named_or_text_only_knowledge_questions_do_not_require_photo(
    question: str,
) -> None:
    assert not needs_an_attached_picture(question)
