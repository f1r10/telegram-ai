"""V1 upload allowlist and technical size checks."""

from pathlib import Path

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def validate_upload(filename: str, content_type: str | None, byte_size: int, max_bytes: int) -> str:
    """Return a normalized content type or raise ValueError for unsafe uploads."""

    suffix = Path(filename).suffix.lower()
    expected = SUPPORTED_EXTENSIONS.get(suffix)
    if expected is None:
        raise ValueError("unsupported_type")
    if byte_size > max_bytes:
        raise ValueError("too_large")
    normalized_type = (content_type or "").split(";", 1)[0].lower()
    if normalized_type and normalized_type not in {expected, "application/octet-stream"}:
        raise ValueError("unsupported_type")
    return expected


def extension_for_content_type(content_type: str) -> str:
    for suffix, supported_type in SUPPORTED_EXTENSIONS.items():
        if supported_type == content_type:
            return suffix
    return ".bin"
