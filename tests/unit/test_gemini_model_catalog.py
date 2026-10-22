"""Model discovery filters generation models; no credentials, HTTP calls or company data."""

import pytest

from office_assistant.scripts.check_gemini_models import available_generation_models


def test_catalog_keeps_only_gemini_generate_content_models() -> None:
    payload: object = {
        "models": [
            {
                "name": "models/gemini-3.8-flash",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            },
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["countTokens"]},
            {
                "name": "models/gemini-3.5-flash-lite",
                "supportedGenerationMethods": ["generateContent"],
            },
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-unknown", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-invalid", "supportedGenerationMethods": "generateContent"},
            {"name": "models/gemini-malformed"},
            {},
            None,
        ]
    }
    assert available_generation_models(payload) == {
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
    }


@pytest.mark.parametrize("invalid", [None, [], "oops", {"models": {}}, {}])
def test_invalid_catalog_response_fails_closed(invalid: object) -> None:
    with pytest.raises(ValueError):
        available_generation_models(invalid)
