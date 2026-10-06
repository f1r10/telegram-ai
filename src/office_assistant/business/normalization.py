"""Conservative, Unicode-safe identity normalization."""

import re
import unicodedata

_PRODUCT_CODE_SEPARATORS = re.compile(r"[\s\-_]+")
_HUMAN_PUNCTUATION = re.compile(r"[.,;:]+")
_WHITESPACE = re.compile(r"\s+")


def _required(value: str, field: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def normalize_product_code(value: str) -> str:
    """Normalize a machine-like product code without transliterating Unicode."""
    normalized = _required(value, "product code").casefold().upper()
    normalized = _PRODUCT_CODE_SEPARATORS.sub("", normalized)
    if not normalized:
        raise ValueError("product code must contain non-separator characters")
    return normalized


def normalize_product_alias(value: str) -> str:
    """Normalize product aliases with the same controlled separators as codes."""
    return normalize_product_code(_required(value, "product alias"))


def normalize_customer_name(value: str) -> str:
    """Normalize human names while preserving letters and business suffixes."""
    normalized = _required(value, "customer name").casefold()
    normalized = _HUMAN_PUNCTUATION.sub(" ", normalized)
    return _WHITESPACE.sub(" ", normalized).strip()


def normalize_customer_alias(value: str) -> str:
    return normalize_customer_name(value)


def normalize_currency(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", normalized):
        raise ValueError("currency must be a three-letter uppercase code")
    return normalized
