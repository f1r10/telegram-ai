"""OCR replay never promotes blank text or trusts tampered source files."""

from hashlib import sha256
from pathlib import Path

from office_assistant.infrastructure.database.models import FileObject, FileObjectState
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.ocr_replay import verified_raw_pdf


def test_replay_refuses_tampered_pdf_and_oversized_source(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    payload = b"%PDF-original-safe-test"
    path = storage.blob_root / "input.pdf"
    path.write_bytes(payload)
    file = FileObject(
        storage_key="blobs/input.pdf",
        original_filename="input.pdf",
        content_type="application/pdf",
        sha256=sha256(payload).hexdigest(),
        byte_size=len(payload),
        state=FileObjectState.ACTIVE,
    )
    assert verified_raw_pdf(storage, file, 50) == path
    assert verified_raw_pdf(storage, file, 10) is None
    path.write_bytes(b"%PDF-other-content")
    assert verified_raw_pdf(storage, file, 50) is None
    path.write_bytes(b"not-a-pdf")
    assert verified_raw_pdf(storage, file, 50) is None


def test_replay_rejects_deleted_file_and_wrong_mime(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    payload = b"%PDF-sample"
    (storage.blob_root / "x.pdf").write_bytes(payload)
    file = FileObject(
        storage_key="blobs/x.pdf",
        original_filename="x.pdf",
        content_type="application/pdf",
        sha256=sha256(payload).hexdigest(),
        byte_size=len(payload),
        state=FileObjectState.DELETED,
    )
    assert verified_raw_pdf(storage, file, 1024) is None
    file.state = FileObjectState.ACTIVE
    file.content_type = "text/plain"
    assert verified_raw_pdf(storage, file, 1024) is None
