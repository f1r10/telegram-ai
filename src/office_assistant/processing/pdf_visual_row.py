"""Conservative visual row-to-caption association for native-text PDF catalogs.

Associates an already-matched PDF XObject occurrence with adjacent printed text
using page coordinates. This is source evidence, NOT a product catalog approval.
Complex pages, OCR-only pages, rotated content and embedded Form XObjects fail
closed rather than inventing a product label.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from pypdf import PdfReader

logger = logging.getLogger(__name__)

MAX_CATALOG_PDF_BYTES = 32 * 1024 * 1024
MAX_CATALOG_PAGES = 250
MAX_CAPTION_LENGTH = 160
MAX_VERTICAL_DISTANCE = 28.0
MIN_COLUMN_GAP = 12.0


def _clean_printed_text(value: str) -> str:
    """Return a short display-only value, not instructions or markup."""

    printable = "".join(
        char
        for char in value
        if char.isprintable() and char not in "\u202a\u202b\u202c\u202d\u202e"
    )
    return " ".join(printable.split())[:MAX_CAPTION_LENGTH].strip()


def _native_layout(
    page: Any,
) -> tuple[list[tuple[float, float, float, float]], list[tuple[float, float, str]]] | None:
    """Collect drawn image boxes and native text anchors in PDF user coordinates."""

    images: list[tuple[float, float, float, float]] = []
    texts: list[tuple[float, float, str]] = []
    try:
        resources = page["/Resources"].get_object()
        xobjects = resources.get("/XObject")
        if xobjects is None:
            return None
        xobjects = xobjects.get_object()
        has_form = False

        def operand_before(
            operator: bytes,
            arguments: list[Any],
            cm: list[float],
            tm: list[float],
        ) -> None:
            nonlocal has_form
            del tm
            if operator != b"Do" or not arguments:
                return
            object_ref = xobjects.get(arguments[0])
            if object_ref is None:
                return
            obj = object_ref.get_object()
            kind = str(obj.get("/Subtype"))
            if kind == "/Form":
                has_form = True
                return
            if kind != "/Image":
                return
            a, b, c, d, e, f = map(float, cm)
            if any(abs(value) > 100_000 for value in (a, b, c, d, e, f)):
                return
            # Only flat, non-rotated image placements can be mapped to a
            # neighboring row without relying on inferred text geometry.
            if abs(b) > 0.1 or abs(c) > 0.1 or abs(a) < 1 or abs(d) < 1:
                has_form = True
                return
            xpoints = (e, e + a, e + c, e + a + c)
            ypoints = (f, f + b, f + d, f + b + d)
            images.append((min(xpoints), max(xpoints), min(ypoints), max(ypoints)))

        def visitor_text(
            text: str,
            cm: list[float],
            tm: list[float],
            font_dict: Any,
            font_size: float,
        ) -> None:
            del font_dict, font_size
            normalized = _clean_printed_text(text)
            if not normalized:
                return
            x = float(cm[0]) * float(tm[4]) + float(cm[2]) * float(tm[5]) + float(cm[4])
            y = float(cm[1]) * float(tm[4]) + float(cm[3]) * float(tm[5]) + float(cm[5])
            if 0 <= x <= 20_000 and -20_000 <= y <= 20_000:
                texts.append((x, y, normalized))

        page.extract_text(visitor_operand_before=operand_before, visitor_text=visitor_text)
        return None if has_form else (images, texts)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OSError) as error:
        logger.warning("PDF row geometry unavailable | error_type=%s", type(error).__name__)
        return None


def _caption_for_image(
    image_rect: tuple[float, float, float, float],
    text_anchors: list[tuple[float, float, str]],
) -> str | None:
    left, right, bottom, top = image_rect
    if right <= left or top <= bottom:
        return None
    center = (bottom + top) / 2
    candidates = [
        (abs(y - center), x, y, text)
        for x, y, text in text_anchors
        if x >= right + MIN_COLUMN_GAP
        and bottom - 12 <= y <= top + 12
        and len(text) >= 4
        and any(char.isalpha() for char in text)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate[0])
    distance, anchor_x, anchor_y, _ = candidates[0]
    if distance > MAX_VERTICAL_DISTANCE:
        return None
    # Two equally-near separate rows are ambiguous; don't pick one arbitrarily.
    for other_distance, other_x, other_y, _ in candidates[1:]:
        if abs(other_y - anchor_y) > 22 and other_distance <= MAX_VERTICAL_DISTANCE:
            return None
        if abs(other_x - anchor_x) > 150 and abs(other_y - anchor_y) < 8:
            return None

    # A product title may wrap to a second line (same text column).
    line_items = [
        (x, y, label)
        for _, x, y, label in candidates
        if abs(y - anchor_y) <= 17
        and abs(x - anchor_x) <= 90
        and abs(y - center) <= MAX_VERTICAL_DISTANCE
    ]
    if not 1 <= len(line_items) <= 3:
        return None
    # PDF text coordinates use a bottom-up Y axis.
    line_items.sort(key=lambda item: (-item[1], item[0]))
    unique: list[str] = []
    for _, _, line in line_items:
        if line not in unique:
            unique.append(line)
    label = _clean_printed_text(" ".join(unique))
    if not 4 <= len(label) <= MAX_CAPTION_LENGTH:
        return None
    if label.casefold().startswith(("mallarin ", "məhsul şəkli", "mehsul sekli")):
        return None
    return label


def read_pdf_visual_row(
    path: Path,
    page_number: int,
    image_index: int,
) -> str | None:
    """Best-effort adjacent caption for the numbered image drawn on a PDF page.

    Caller must enforce actor authorization, ACTIVE FileObject and original
    storage-path restrictions BEFORE invoking this function.
    """

    if page_number < 1 or image_index < 1 or image_index > 100:
        return None
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_CATALOG_PDF_BYTES:
            return None
        reader = PdfReader(str(path), strict=True)
        if not 0 < len(reader.pages) <= MAX_CATALOG_PAGES:
            return None
        if page_number > len(reader.pages):
            return None
        layout = _native_layout(cast(Any, reader.pages[page_number - 1]))
        if layout is None:
            return None
        image_rects, texts = layout
        if image_index > len(image_rects):
            return None
        return _caption_for_image(image_rects[image_index - 1], texts)
    except Exception as error:  # noqa: BLE001 - a malformed user PDF must not break photo search
        logger.warning("PDF row label extraction skipped | error_type=%s", type(error).__name__)
        return None
