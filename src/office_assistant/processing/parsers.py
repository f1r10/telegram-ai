"""Targeted, deterministic parser adapters and their registry."""

# Third-party document libraries do not all ship complete Pyright stubs. The
# adapter boundary keeps that uncertainty out of the normalized model/worker.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportPrivateUsage=false, reportUnknownArgumentType=false

import math
import re
import zipfile
from datetime import datetime
from pathlib import Path
from posixpath import normpath
from typing import Any, Protocol, cast
from xml.etree import ElementTree

import pypdf
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from openpyxl.cell.cell import TYPE_FORMULA
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from pypdf import PdfReader
from pypdf.generic import ContentStream

from office_assistant.infrastructure.database.models import ImageAssetOrigin
from office_assistant.processing.image_metadata import image_signature
from office_assistant.processing.image_ocr import ImageOcrService
from office_assistant.processing.models import ContentBlock, EmbeddedImage, ParsedDocument
from office_assistant.processing.pdf_images import (
    SampleImageError,
    pdf_name,
    picture_bytes,
    soft_mask_note,
)
from office_assistant.processing.pdf_ocr import PdfOcrService

# A picture inside a document is untrusted input like every other byte the bot reads, so extraction
# is bounded: one payload and one count cap keep a hostile file from turning a parse into a disk
# write marathon. Both caps are ASSUMPTION values (Master Spec §23 leaves them open, Q29).
MAX_EMBEDDED_IMAGE_BYTES = 8 * 1024 * 1024
MAX_EMBEDDED_IMAGES_PER_DOCUMENT = 200

#: Phase 2 M6: a Word heading style's depth (``Heading 1`` -> 1). The paragraph's own
#: ``outlineLvl`` wins when it is present; see docs/OPEN-QUESTIONS.md Q60.
_HEADING_STYLE = re.compile(r"heading\s+(\d+)", re.IGNORECASE)

#: The document properties python-docx exposes. Only the non-empty ones are recorded, so a plain
#: document does not gain a wall of empty strings. Datetimes are serialised as ISO 8601.
_CORE_PROPERTY_FIELDS = (
    "title",
    "subject",
    "author",
    "last_modified_by",
    "keywords",
    "category",
    "comments",
    "content_status",
    "identifier",
    "language",
    "version",
    "created",
    "modified",
    "revision",
)


def _style_depth(style: str) -> int | None:
    """The depth of a Word heading style (``Heading 3`` -> 3), or ``None`` for any other style."""

    match = _HEADING_STYLE.search(style)
    return int(match.group(1)) if match is not None else None


def _confidence_summary(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-page extraction sources and OCR scores for one parse run (Phase 2 M8).

    Native pages contribute no score, so the mean is over OCR pages only and is ``None`` when no
    page reported one. Nothing here compares a score against a threshold: recording is not acting.
    """

    scores: list[float] = []
    for entry in sources:
        value = entry.get("confidence")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            scores.append(float(value))
    return {
        "pages": sources,
        "native_pages": sum(1 for entry in sources if entry.get("source") == "native"),
        "ocr_pages": sum(1 for entry in sources if entry.get("source") == "tesseract"),
        "mean_ocr_confidence": round(sum(scores) / len(scores), 4) if scores else None,
    }


_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_SPREADSHEET_DRAWING_NAMESPACE = (
    "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
)
_DRAWML_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"
_SPREADSHEETML_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XML_NAMESPACES = {"a": _DRAWML_NAMESPACE, "xdr": _SPREADSHEET_DRAWING_NAMESPACE}
_BLIP_TAG = qn("a:blip")
_RELATIONSHIP_ID_ATTRIBUTE = qn("r:embed")
_RELATIONSHIP_ID_FIELD = f"{{{_RELATIONSHIP_NAMESPACE}}}id"
_SHEET_TAG = f"{{{_SPREADSHEETML_NAMESPACE}}}sheet"
_RELATIONSHIP_TAG = f"{{{_PACKAGE_RELATIONSHIP_NAMESPACE}}}Relationship"
_WORKSHEET_RELATIONSHIP = f"{_RELATIONSHIP_NAMESPACE}/worksheet"
_DRAWING_RELATIONSHIP = f"{_RELATIONSHIP_NAMESPACE}/drawing"
_IMAGE_RELATIONSHIP = f"{_RELATIONSHIP_NAMESPACE}/image"

# The six header and footer roles one Word section can define, as the attribute that reaches them
# and the role a locator should name them by.
_SECTION_DEFINITIONS = (
    ("header", "default_header"),
    ("first_page_header", "first_page_header"),
    ("even_page_header", "even_page_header"),
    ("footer", "default_footer"),
    ("first_page_footer", "first_page_footer"),
    ("even_page_footer", "even_page_footer"),
)


class _ExtractedImages:
    """One document's pictures, bounded, plus an honest record of every payload left behind."""

    def __init__(self, warnings: list[str]) -> None:
        self.images: list[EmbeddedImage] = []
        self.warnings = warnings
        self._stopped = False

    def add(self, locator: dict[str, Any], data: bytes, description: str) -> bool:
        """Keep one picture, or say out loud why it was left behind. Returns whether it was kept."""

        if len(self.images) >= MAX_EMBEDDED_IMAGES_PER_DOCUMENT:
            if not self._stopped:
                self._stopped = True
                self.warnings.append(
                    f"Picture extraction stopped at {MAX_EMBEDDED_IMAGES_PER_DOCUMENT} images"
                )
            return False
        if len(data) > MAX_EMBEDDED_IMAGE_BYTES:
            self.warnings.append(
                f"{description}: {len(data)} bytes exceeds the {MAX_EMBEDDED_IMAGE_BYTES} byte cap"
            )
            return False
        signature = image_signature(data)
        if signature is None:
            self.warnings.append(
                f"{description}: bytes are not a PNG or JPEG this build can describe"
            )
            return False
        self.images.append(EmbeddedImage(locator, data, signature.mime_type))
        return True


class DocumentParser(Protocol):
    name: str
    version: str
    #: Provenance recorded for pictures found inside this format, or ``None`` when the format
    #: carries none. The standalone upload of an image is registered by the worker, not here.
    embedded_origin: ImageAssetOrigin | None

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument: ...


class TxtParser:
    name = "txt"
    version = "1.0"
    embedded_origin: ImageAssetOrigin | None = None

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        content = path.read_text(encoding="utf-8-sig")
        blocks = (
            [
                ContentBlock(
                    sequence=1,
                    block_type="plain_text",
                    text=content,
                    locator={"line_start": 1, "line_end": max(1, len(content.splitlines()))},
                )
            ]
            if content
            else []
        )
        return ParsedDocument(source_file_id, self.name, self.version, media_type, {}, blocks)


class PdfParser:
    name = "pypdf"
    version = pypdf.__version__
    embedded_origin: ImageAssetOrigin | None = ImageAssetOrigin.PDF_EMBEDDED

    def __init__(
        self,
        ocr_service: PdfOcrService | None = None,
        *,
        capture_layout: bool = False,
        capture_confidence: bool = False,
    ) -> None:
        self.ocr_service = ocr_service
        #: Phase 2 M8: name each page's extraction source and record the OCR engine's confidence
        #: when it reported one. A native-text page records ``extraction: "native"`` and no score.
        #: Off by default, so the pre-M8 block metadata (empty) is unchanged.
        self.capture_confidence = capture_confidence
        #: Phase 2 M3: record each page's text-line geometry so the structure layer can compute a
        #: geometric reading order and detect repeated bands. Off by default, so a deployment that
        #: has not opted in keeps byte-for-byte the pre-M3 blocks (no extra metadata is emitted).
        self.capture_layout = capture_layout

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        blocks: list[ContentBlock] = []
        text_warnings: list[str] = []
        extraction_warnings: list[str] = []
        page_layouts: list[dict[str, Any]] = []
        page_sources: list[dict[str, Any]] = []
        reader = PdfReader(str(path), strict=True)
        ocr = self.ocr_service.session(path) if self.ocr_service is not None else None
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception:  # noqa: BLE001 - a bad page must not discard the document
                extraction_warnings.append(f"Page {page_number}: native text extraction failed")
                text = ""
            if text:
                if self.capture_layout:
                    layout = _page_layout(page, page_number, extraction_warnings)
                    if layout is not None:
                        # Additive only: the page's text and locator are exactly what they were.
                        # The geometry is a document-level sidecar, so ``blocks`` never changes.
                        page_layouts.append(layout)
                native_metadata: dict[str, Any] = {}
                if self.capture_confidence:
                    native_metadata["extraction"] = "native"
                    page_sources.append(
                        {"page": page_number, "source": "native", "confidence": None}
                    )
                blocks.append(
                    ContentBlock(
                        len(blocks) + 1,
                        "page_text",
                        str(cast(Any, text)),
                        {"page": page_number},
                        native_metadata,
                    )
                )
                continue
            if ocr is not None:
                result = ocr.read_page(page_number)
                if result.text:
                    ocr_metadata: dict[str, Any] = {}
                    if self.capture_confidence:
                        ocr_metadata["extraction"] = "tesseract"
                        if result.confidence is not None:
                            ocr_metadata["confidence"] = result.confidence
                        page_sources.append(
                            {
                                "page": page_number,
                                "source": "tesseract",
                                "confidence": result.confidence,
                            }
                        )
                    blocks.append(
                        ContentBlock(
                            len(blocks) + 1,
                            "page_ocr",
                            result.text,
                            {"page": page_number, "extraction": "tesseract"},
                            ocr_metadata,
                        )
                    )
                    continue
                text_warnings.append(
                    f"Page {page_number} contains no searchable text"
                    f" (OCR: {result.reason or 'unavailable'})"
                )
            else:
                text_warnings.append(
                    f"Page {page_number} contains no extractable text; OCR may be required"
                )
        image_warnings: list[str] = []
        images = self._embedded_images(reader, image_warnings)
        metadata: dict[str, Any] = {}
        if ocr is not None:
            metadata["ocr"] = {
                "engine": "tesseract",
                "attempted_pages": ocr.attempted_pages,
                "recovered_pages": ocr.recovered_pages,
                "unresolved_pages": len(text_warnings),
            }
        if page_layouts:
            # Phase 2 M3: per-line geometry of every page that carried native text, for the
            # structure layer's geometric reading order and repeated-band detection. Recorded at
            # document level, never on a block, so ``blocks`` stays exactly as it was.
            metadata["layout"] = {"pages": page_layouts}
        if page_sources:
            # Phase 2 M8: the parse run's confidence summary. Recorded, never acted on: no
            # threshold is applied here (consumption is Phase 5).
            metadata["confidence"] = _confidence_summary(page_sources)
        return ParsedDocument(
            source_file_id,
            self.name,
            self.version,
            media_type,
            metadata,
            blocks,
            [*text_warnings, *extraction_warnings, *image_warnings],
            requires_ocr=bool(text_warnings),
            embedded_images=images,
        )

    def _embedded_images(self, reader: PdfReader, warnings: list[str]) -> list[EmbeddedImage]:
        """Pictures the page content stream actually draws, in draw order.

        Only drawn XObjects are recorded: a page resource can name a picture the page never paints,
        and an occurrence claiming such a page would be an invented locator (§23.5). The page's
        ``image_index`` counts every drawn picture, including one this build cannot store, so the
        number a run records does not move when a reader learns a new format.
        """

        collected = _ExtractedImages(warnings)
        for page_number, page in enumerate(reader.pages, start=1):
            operations = self._page_operations(page, page_number, warnings)
            if operations is None:
                continue
            inline = sum(1 for _, operator in operations if operator == b"INLINE IMAGE")
            if inline:
                warnings.append(
                    f"Page {page_number}: {inline} inline image(s) are not stored as picture assets"
                )
            resources = _xobject_resources(page)
            index = 0
            for name in _drawn_names(operations):
                stream = resources.get(name)
                if stream is None:
                    continue
                subtype = pdf_name(stream.get("/Subtype"))
                if subtype == "/Image":
                    index += 1
                    self._collect(
                        collected,
                        stream,
                        _page_locator(page_number, index, name),
                        f"Page {page_number} picture {name}",
                    )
                elif subtype == "/Form":
                    index = self._collect_form_images(
                        collected, reader, stream, page_number, name, index
                    )
        return collected.images

    def _collect_form_images(
        self,
        collected: _ExtractedImages,
        reader: PdfReader,
        form: Any,
        page_number: int,
        form_name: str,
        index: int,
    ) -> int:
        """One Form level deep: the form's own draw order says which pictures it paints."""

        try:
            operations = list(ContentStream(form, reader).operations)
        except Exception as error:  # noqa: BLE001 - a damaged form must not cost the whole page
            collected.warnings.append(
                f"Page {page_number} form {form_name}: content unreadable ({type(error).__name__}),"
                " its pictures are not recorded"
            )
            return index
        resources = _xobject_resources(form)
        for name in _drawn_names(operations):
            stream = resources.get(name)
            if stream is None or pdf_name(stream.get("/Subtype")) != "/Image":
                continue
            index += 1
            locator = _page_locator(page_number, index, form_name)
            locator["form_xobject"] = name
            description = f"Page {page_number} picture {form_name}{name}"
            self._collect(collected, stream, locator, description)
        return index

    @staticmethod
    def _collect(
        collected: _ExtractedImages,
        stream: Any,
        locator: dict[str, Any],
        description: str,
    ) -> None:
        try:
            data = picture_bytes(stream, MAX_EMBEDDED_IMAGE_BYTES)
        except SampleImageError as error:
            collected.warnings.append(f"{description}: {error}")
            return
        # A mask is worth naming only once a picture was kept: if nothing was stored, no stored
        # picture left a mask behind.
        note = soft_mask_note(stream) if collected.add(locator, data, description) else None
        if note is not None:
            collected.warnings.append(f"{description}: {note}")

    @staticmethod
    def _page_operations(
        page: Any, page_number: int, warnings: list[str]
    ) -> list[tuple[Any, bytes]] | None:
        try:
            contents = page.get_contents()
            return list(contents.operations) if contents is not None else []
        except Exception as error:  # noqa: BLE001 - an unreadable page must not fail the document
            warnings.append(
                f"Page {page_number}: content stream unreadable ({type(error).__name__}),"
                " its pictures are not recorded"
            )
            return None


def _drawn_names(operations: list[tuple[Any, bytes]]) -> list[str]:
    names: list[str] = []
    for operands, operator in operations:
        if operator == b"Do" and operands:
            names.append(pdf_name(operands[0]))
    return [name for name in names if name]


def _xobject_resources(holder: Any) -> dict[str, Any]:
    resources = holder.get_inherited("/Resources") if hasattr(holder, "get_inherited") else None
    if resources is None:
        resources = holder.get("/Resources")
    if resources is None:
        return {}
    resolved = resources.get_object() if hasattr(resources, "get_object") else resources
    xobjects = resolved.get("/XObject")
    if xobjects is None:
        return {}
    xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
    if not hasattr(xobjects, "items"):
        return {}
    return {
        str(key): (value.get_object() if hasattr(value, "get_object") else value)
        for key, value in xobjects.items()
    }


def _page_locator(page_number: int, image_index: int, name: str) -> dict[str, Any]:
    return {"page": page_number, "image_index": image_index, "xobject": name}


#: A drawn path segment shorter than this (PDF points) is a glyph stroke or a tick, not a table
#: rule, so it is not recorded as a grid line. ASSUMPTION (docs/OPEN-QUESTIONS.md Q58).
_RULE_MIN_LENGTH = 8.0
#: Two coordinates within this distance (PDF points) are the same rule or the same grid line —
#: a hand-authored rule rarely lands on an exact integer. ASSUMPTION (Q58).
_RULE_TOLERANCE = 0.5


def _append_rule(segments: list[list[float]], x0: float, y0: float, x1: float, y1: float) -> None:
    """Keep one axis-aligned segment long enough to be a table rule; drop everything else."""

    if abs(y1 - y0) <= _RULE_TOLERANCE and abs(x1 - x0) >= _RULE_MIN_LENGTH:
        segments.append([round(min(x0, x1), 2), round(y0, 2), round(max(x0, x1), 2), round(y0, 2)])
    elif abs(x1 - x0) <= _RULE_TOLERANCE and abs(y1 - y0) >= _RULE_MIN_LENGTH:
        segments.append([round(x0, 2), round(min(y0, y1), 2), round(x0, 2), round(max(y0, y1), 2)])


def _page_rules(page: Any) -> list[list[float]] | None:
    """The axis-aligned rule segments a page draws, for ruled-table detection (Phase 2 M4).

    A bordered table is a drawn grid, so the structure layer needs the page's straight rules. The
    content stream is read *without* applying any ``cm`` transform, so a page that scales its
    content with ``cm`` reports rules in un-transformed space — the documented assumption for the
    generated fixtures (Q58). Only ``m``/``l`` paths and ``re`` rectangles are read; a curve is
    never an axis-aligned rule. ``None`` (never a crash) is returned when the stream is unreadable,
    so the caller can record an honest warning instead of silently losing a possible table.
    """

    try:
        contents = page.get_contents()
        operations = list(contents.operations) if contents is not None else []
    except Exception:  # noqa: BLE001 - geometry is optional; a bad page keeps parser order
        return None
    segments: list[list[float]] = []
    current: tuple[float, float] | None = None
    for operands, operator in operations:
        if operator == b"m" and len(operands) >= 2:
            current = (_finite_float(operands[0]), _finite_float(operands[1]))
        elif operator == b"l" and len(operands) >= 2 and current is not None:
            x, y = _finite_float(operands[0]), _finite_float(operands[1])
            _append_rule(segments, current[0], current[1], x, y)
            current = (x, y)
        elif operator == b"re" and len(operands) >= 4:
            x0 = _finite_float(operands[0])
            y0 = _finite_float(operands[1])
            x1 = x0 + _finite_float(operands[2])
            y1 = y0 + _finite_float(operands[3])
            _append_rule(segments, x0, y0, x1, y0)
            _append_rule(segments, x0, y1, x1, y1)
            _append_rule(segments, x0, y0, x0, y1)
            _append_rule(segments, x1, y0, x1, y1)
    return segments


def _page_layout(page: Any, page_number: int, warnings: list[str]) -> dict[str, Any] | None:
    """One PDF page's text-fragment geometry, or ``None`` when it cannot be read.

    Phase 2 M3: a content stream writes a page's text in the writer's order, which for a
    multi-column page interleaves the columns (audit gap G7). Recording where each text fragment
    sits lets the structure layer recover a geometric reading order and spot repeated bands. This
    is read-only: the page's own text and locator are produced by a separate, unchanged call.

    Phase 2 M4 adds ``rules`` — the page's drawn straight segments — but only when the page has at
    least one, so a page with no rules records byte-identical geometry to M3. A stream that cannot
    be read records a warning instead of losing a possible table silently.
    """

    fragments: list[dict[str, Any]] = []

    def visit(text: str, cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        value = str(text).strip()
        if not value:
            return
        origin = _text_origin(tm) or _text_origin(cm)
        if origin is None:
            return
        x, y = origin
        size = _finite_float(font_size)
        fragments.append({"text": value, "x": x, "y": y, "size": round(size, 2)})

    try:
        page.extract_text(visitor_text=visit)
    except Exception:  # noqa: BLE001 - geometry is optional; a bad page keeps parser order
        return None
    if not fragments:
        return None
    width = _finite_float(getattr(getattr(page, "mediabox", None), "width", None))
    height = _finite_float(getattr(getattr(page, "mediabox", None), "height", None))
    if width <= 0 or height <= 0:
        return None
    layout: dict[str, Any] = {
        "page": page_number,
        "width": round(width, 2),
        "height": round(height, 2),
        "lines": fragments,
    }
    rules = _page_rules(page)
    if rules is None:
        warnings.append(
            f"Page {page_number}: content stream unreadable, its table rules are not recorded"
        )
    elif rules:
        layout["rules"] = rules
    return layout


def _text_origin(matrix: Any) -> tuple[float, float] | None:
    """``(x, y)`` from a PDF text/current matrix, or ``None`` when the matrix is unusable."""

    if matrix is None:
        return None
    try:
        x = float(matrix[4])
        y = float(matrix[5])
    except (TypeError, ValueError, IndexError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return round(x, 2), round(y, 2)


def _finite_float(value: Any) -> float:
    """A finite float for ``value``, or ``0.0`` when it is missing or not a finite number."""

    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _zip_root(archive: zipfile.ZipFile, name: str) -> ElementTree.Element | None:
    """Parse one XML member of a package, or report nothing for a member that is absent."""

    if name not in archive.namelist():
        return None
    try:
        return ElementTree.fromstring(archive.read(name))
    except ElementTree.ParseError:
        return None


def _relationships(archive: zipfile.ZipFile, source_part: str, kind: str) -> list[tuple[str, str]]:
    """``(id, target)`` for every relationship of one type held by a package part."""

    directory, _, leaf = source_part.rpartition("/")
    root = _zip_root(archive, f"{directory}/_rels/{leaf}.rels")
    if root is None:
        return []
    return [
        (
            relationship.get("Id") or "",
            relationship.get("Target") or "",
        )
        for relationship in root.iter(_RELATIONSHIP_TAG)
        if relationship.get("Type") == kind
    ]


def _resolve_part(source_part: str, target: str) -> str:
    """Turn a relationship target into a package member name, absolute or part-relative."""

    if target.startswith("/"):
        return normpath(target.removeprefix("/"))
    directory, _, _ = source_part.rpartition("/")
    return normpath(f"{directory}/{target}") if directory else normpath(target)


def _zip_payload(
    archive: zipfile.ZipFile, name: str, description: str, warnings: list[str]
) -> bytes | None:
    """Read one member, refusing to decompress more than a picture could ever be."""

    try:
        declared_size = archive.getinfo(name).file_size
    except KeyError:
        warnings.append(f"{description}: member {name} is absent from this package")
        return None
    if declared_size > MAX_EMBEDDED_IMAGE_BYTES:
        warnings.append(
            f"{description}: {declared_size} declared bytes exceeds the {MAX_EMBEDDED_IMAGE_BYTES}"
            " byte cap"
        )
        return None
    return archive.read(name)


def _anchor_cell(anchor: ElementTree.Element) -> str | None:
    """The cell a drawing anchor sits on, or ``None`` when the anchor declares no cell."""

    origin = anchor.find("xdr:from", _XML_NAMESPACES)
    if origin is None:
        return None
    column = origin.find("xdr:col", _XML_NAMESPACES)
    row = origin.find("xdr:row", _XML_NAMESPACES)
    if column is None or row is None or not (column.text or "").isdigit():
        return None
    if not (row.text or "").isdigit():
        return None
    return f"{get_column_letter(int(column.text) + 1)}{int(row.text) + 1}"


class DocxParser:
    name = "python-docx"
    version = "1.2.0"
    embedded_origin: ImageAssetOrigin | None = ImageAssetOrigin.DOCX_EMBEDDED

    def __init__(self, *, capture_structure: bool = False) -> None:
        #: Phase 2 M6: record the outline depth, list membership, table cell coordinates and the
        #: document's core properties. Off by default, so an opted-out deployment produces exactly
        #: the pre-M6 blocks and an empty document metadata.
        self.capture_structure = capture_structure

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        document = Document(str(path))
        blocks: list[ContentBlock] = []
        warnings: list[str] = []
        collected = _ExtractedImages(warnings)
        self._collect_block_images(collected, document.part, document.element.body, {}, "body")
        self._collect_section_images(collected, document)
        paragraph_index = 0
        table_index = 0
        for element in document.element.body.iterchildren():
            if element.tag.endswith("}p"):
                paragraph = Paragraph(cast(Any, element), document)
                current_paragraph_index = paragraph_index
                paragraph_index += 1
                text = paragraph.text.strip()
                if text:
                    style = paragraph.style.name if paragraph.style else ""
                    block_type = "heading" if style.lower().startswith("heading") else "paragraph"
                    block_metadata: dict[str, Any] = {}
                    if self.capture_structure:
                        if block_type == "heading":
                            block_metadata.update(self._heading_metadata(paragraph, style))
                        listing = self._list_metadata(paragraph, style)
                        if listing is not None:
                            # A list paragraph keeps its text and locator; only its type is named.
                            block_type = "list_item"
                            block_metadata.update(listing)
                    blocks.append(
                        ContentBlock(
                            len(blocks) + 1,
                            block_type,
                            text,
                            {"paragraph_index": current_paragraph_index},
                            block_metadata,
                        )
                    )
            elif element.tag.endswith("}tbl"):
                table = Table(cast(Any, element), document)
                current_table_index = table_index
                table_index += 1
                for row_index, row in enumerate(table.rows, start=1):
                    values = [cell.text.strip() for cell in row.cells]
                    if any(values):
                        row_metadata: dict[str, Any] = {"cells": values}
                        if self.capture_structure:
                            # Grid coordinates, aligned index-for-index with ``cells``. A merged
                            # cell repeats its text at each grid position python-docx reports (Q60).
                            row_metadata["cell_coordinates"] = [
                                {"row": row_index, "column": column_index}
                                for column_index in range(1, len(values) + 1)
                            ]
                        blocks.append(
                            ContentBlock(
                                len(blocks) + 1,
                                "table_row",
                                " | ".join(values),
                                {
                                    "table_index": current_table_index,
                                    "row": row_index,
                                },
                                row_metadata,
                            )
                        )
        metadata: dict[str, Any] = {}
        if self.capture_structure:
            properties = self._core_properties(document)
            if properties:
                metadata["core_properties"] = properties
        return ParsedDocument(
            source_file_id,
            self.name,
            self.version,
            media_type,
            metadata,
            blocks,
            warnings,
            embedded_images=collected.images,
        )

    @staticmethod
    def _heading_metadata(paragraph: Paragraph, style: str) -> dict[str, Any]:
        """A heading's depth and where it came from: ``outlineLvl`` first, then the style depth."""

        properties = paragraph._p.pPr
        outline = properties.outlineLvl if properties is not None else None
        if outline is not None and outline.val is not None:
            return {"level": int(outline.val) + 1, "level_source": "outline"}
        depth = _style_depth(style)
        if depth is not None:
            return {"level": depth, "level_source": "style"}
        return {"level": 1, "level_source": "default"}

    @staticmethod
    def _list_metadata(paragraph: Paragraph, style: str) -> dict[str, Any] | None:
        """List membership, level and marker kind, or ``None`` when the paragraph is body text.

        A paragraph-level ``w:numPr`` is the strongest signal; ``numId=0`` means numbering was
        explicitly removed and is **not** a list. The default template's ``List Number`` /
        ``List Bullet`` styles carry no paragraph-level ``numPr``, so the style name is the
        fallback. ``List Paragraph`` alone is a container style, not a list, and is ignored.
        """

        properties = paragraph._p.pPr
        numbering = properties.numPr if properties is not None else None
        level: int | None = None
        numbering_id: int | None = None
        if numbering is not None:
            if numbering.ilvl is not None and numbering.ilvl.val is not None:
                level = int(numbering.ilvl.val) + 1
            if numbering.numId is not None and numbering.numId.val is not None:
                numbering_id = int(numbering.numId.val)
        if numbering is not None and numbering_id is not None:
            if numbering_id == 0:
                return None
            return {"level": level or 1, "marker": "numbered", "numbering_id": numbering_id}
        lowered = style.strip().casefold()
        marker = None
        if lowered.startswith("list number"):
            marker = "numbered"
        elif lowered.startswith("list bullet"):
            marker = "bulleted"
        if marker is None:
            return None
        return {"level": level or _style_depth(style) or 1, "marker": marker}

    @staticmethod
    def _core_properties(document: Any) -> dict[str, Any]:
        """The document's non-empty core properties, with datetimes as ISO 8601 strings."""

        properties = document.core_properties
        collected: dict[str, Any] = {}
        for name in _CORE_PROPERTY_FIELDS:
            value = getattr(properties, name, None)
            if isinstance(value, datetime):
                collected[name] = value.isoformat()
            elif isinstance(value, str):
                if value.strip():
                    collected[name] = value
            elif value is not None:
                collected[name] = value
        return collected

    @classmethod
    def _collect_section_images(cls, collected: _ExtractedImages, document: Any) -> None:
        """Pictures a section's own header or footer carries, located in the part that holds them.

        A header is a package part, not a place in the body, so its locator names the part and the
        role it plays rather than pretending to be body text. A section that links its header to the
        previous one owns no definition and therefore no picture of its own: the section that does
        define it has already been read, and a second sighting invented here would be a duplicate
        claim (§23.5).
        """

        for section_index, section in enumerate(document.sections):
            for attribute, role in _SECTION_DEFINITIONS:
                holder = getattr(section, attribute)
                if holder.is_linked_to_previous:
                    continue
                part = holder.part
                cls._collect_block_images(
                    collected,
                    part,
                    part.element,
                    {
                        "part": str(part.partname).lstrip("/"),
                        "section_index": section_index,
                        "role": role,
                    },
                    f"{role} of section {section_index + 1}",
                )

    @staticmethod
    def _collect_block_images(
        collected: _ExtractedImages,
        part: Any,
        root: Any,
        context: dict[str, Any],
        description_prefix: str,
    ) -> None:
        """Every picture in a run of block elements, each at the locator of its own place.

        Rows and cells come from the XML rather than ``table.rows[i].cells[j]``: a merged cell is
        reported once per grid position it spans, which would let one picture claim several homes.
        """

        paragraph_index = 0
        table_index = 0
        for element in root.iterchildren():
            if element.tag.endswith("}p"):
                current_paragraph_index = paragraph_index
                paragraph_index += 1
                DocxParser._collect_images(
                    collected,
                    part,
                    element,
                    {**context, "paragraph_index": current_paragraph_index},
                    f"{description_prefix} paragraph {current_paragraph_index}",
                )
            elif element.tag.endswith("}tbl"):
                current_table_index = table_index
                table_index += 1
                for row_index, row_element in enumerate(element.findall(qn("w:tr")), start=1):
                    for column_index, cell_element in enumerate(row_element.findall(qn("w:tc")), 1):
                        DocxParser._collect_images(
                            collected,
                            part,
                            cell_element,
                            {
                                **context,
                                "table_index": current_table_index,
                                "row": row_index,
                                "column": column_index,
                            },
                            f"{description_prefix} table {current_table_index} "
                            f"cell {row_index}:{column_index}",
                        )

    @staticmethod
    def _collect_images(
        collected: _ExtractedImages,
        part: Any,
        element: Any,
        context: dict[str, Any],
        description: str,
    ) -> None:
        """Every picture this element draws, in document order, through its relationship id."""

        related_parts = part.related_parts
        for image_index, blip in enumerate(element.iter(_BLIP_TAG), start=1):
            relationship_id = blip.get(_RELATIONSHIP_ID_ATTRIBUTE)
            if not relationship_id or relationship_id not in related_parts:
                collected.warnings.append(
                    f"{description} picture {image_index}: relationship "
                    f"{relationship_id or 'none'} is absent from this package"
                )
                continue
            locator = {**context, "image_index": image_index, "relationship_id": relationship_id}
            collected.add(
                locator,
                related_parts[relationship_id].blob,
                f"{description} picture {image_index}",
            )


class XlsxParser:
    name = "openpyxl"
    version = "3.1.5"
    embedded_origin: ImageAssetOrigin | None = ImageAssetOrigin.XLSX_EMBEDDED

    def __init__(self, *, capture_cached_values: bool = False) -> None:
        #: Phase 2 M5: read the workbook a second time with ``data_only=True`` so a formula cell
        #: records the result the file cached beside the formula string. Off by default, so an
        #: opted-out deployment keeps exactly the row metadata pre-M5 produced (golden contract).
        self.capture_cached_values = capture_cached_values

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        workbook = load_workbook(path, data_only=False)
        cached_workbook: Any = None
        blocks: list[ContentBlock] = []
        warnings: list[str] = []
        metadata: dict[str, Any] = {}
        if self.capture_cached_values:
            try:
                cached_workbook = load_workbook(path, data_only=True)
            except Exception:  # noqa: BLE001 - cached values enrich, they never gate a parse
                # A workbook that opens one way and not the other is surfaced, not guessed at.
                warnings.append(
                    "Cached spreadsheet values are unavailable: the workbook could not be "
                    "reopened in data-only mode"
                )
        try:
            for sheet in workbook.worksheets:
                cached_sheet = (
                    cached_workbook[sheet.title]
                    if cached_workbook is not None and sheet.title in cached_workbook.sheetnames
                    else None
                )
                self._parse_sheet(sheet, cached_sheet, blocks, warnings)
            if cached_workbook is not None:
                # Additive document-level metadata: where a merged region sits and which table
                # ranges the workbook declares. The flat row blocks keep their own locators.
                metadata["xlsx"] = {
                    "sheets": [self._sheet_metadata(sheet) for sheet in workbook.worksheets]
                }
        finally:
            workbook.close()
            if cached_workbook is not None:
                cached_workbook.close()
        collected = _ExtractedImages(warnings)
        self._collect_images(path, collected)
        return ParsedDocument(
            source_file_id,
            self.name,
            self.version,
            media_type,
            metadata,
            blocks,
            warnings,
            embedded_images=collected.images,
        )

    @staticmethod
    def _sheet_metadata(sheet: Worksheet) -> dict[str, Any]:
        """Merged regions and declared table ranges for one sheet (M5).

        Empty collections are omitted so a plain workbook adds no structural noise. A merged region
        is reported with its range and the anchor cell that holds its value, because openpyxl
        returns ``None`` for every other cell in the merged area.
        """

        entry: dict[str, Any] = {"sheet": sheet.title}
        merged_regions = [
            {"range": str(cells_range), "anchor": str(cells_range).split(":", 1)[0]}
            for cells_range in sheet.merged_cells.ranges
        ]
        if merged_regions:
            entry["merged_regions"] = merged_regions
        declared_tables = [
            {"name": table.name, "range": table.ref} for table in sheet.tables.values()
        ]
        if declared_tables:
            entry["declared_tables"] = declared_tables
        return entry

    @classmethod
    def _collect_images(cls, path: Path, collected: _ExtractedImages) -> None:
        """Pictures anchored on a worksheet, read from the package rather than a live workbook.

        openpyxl's drawing reader returns no pictures at all when Pillow is absent
        (``reader.drawings.find_images`` gives up before it builds one), and Pillow is not a
        dependency of this build (Q28). Following the drawing parts directly also reads the anchor
        the drawing itself declares, which is what §23.5 asks a locator to name.
        """

        with zipfile.ZipFile(path) as archive:
            for sheet_name, sheet_part in cls._sheets(archive, collected):
                drawings: list[str] = []
                for _, target in _relationships(archive, sheet_part, _DRAWING_RELATIONSHIP):
                    part = _resolve_part(sheet_part, target)
                    if part not in drawings:
                        drawings.append(part)
                for drawing_part in drawings:
                    root = _zip_root(archive, drawing_part)
                    if root is None:
                        collected.warnings.append(
                            f"Sheet {sheet_name}: drawing part {drawing_part} is unreadable"
                        )
                        continue
                    relationships = {
                        relationship_id: _resolve_part(drawing_part, target)
                        for relationship_id, target in _relationships(
                            archive, drawing_part, _IMAGE_RELATIONSHIP
                        )
                    }
                    image_index = 0
                    for anchor in root:
                        anchor_cell = _anchor_cell(anchor)
                        for blip in anchor.iter(_BLIP_TAG):
                            image_index += 1
                            relationship_id = blip.get(_RELATIONSHIP_ID_ATTRIBUTE)
                            member = relationships.get(relationship_id) if relationship_id else None
                            description = f"Sheet {sheet_name} picture {image_index}"
                            if member is None:
                                collected.warnings.append(
                                    f"{description}: relationship "
                                    f"{relationship_id or 'none'} is absent from this package"
                                )
                                continue
                            locator: dict[str, Any] = {
                                "sheet": sheet_name,
                                "image_index": image_index,
                                "relationship_id": relationship_id,
                            }
                            if anchor_cell is not None:
                                locator["anchor_cell"] = anchor_cell
                            data = _zip_payload(archive, member, description, collected.warnings)
                            if data is not None:
                                collected.add(locator, data, description)

    @staticmethod
    def _sheets(archive: zipfile.ZipFile, collected: _ExtractedImages) -> list[tuple[str, str]]:
        """Worksheet names with their part paths, in the order the workbook lists them."""

        root = _zip_root(archive, "xl/workbook.xml")
        if root is None:
            collected.warnings.append("Workbook part xl/workbook.xml is unreadable")
            return []
        names = {
            sheet.get(_RELATIONSHIP_ID_FIELD): (sheet.get("name") or "")
            for sheet in root.iter(_SHEET_TAG)
        }
        sheets: list[tuple[str, str]] = []
        workbook_rels = _relationships(archive, "xl/workbook.xml", _WORKSHEET_RELATIONSHIP)
        for relationship_id, target in workbook_rels:
            name = names.get(relationship_id)
            if name is None:
                collected.warnings.append(
                    f"Worksheet relationship {relationship_id} has no sheet entry"
                )
                continue
            sheets.append((name, _resolve_part("xl/workbook.xml", target)))
        return sheets

    @staticmethod
    def _parse_sheet(
        sheet: Worksheet,
        cached_sheet: Worksheet | None,
        blocks: list[ContentBlock],
        warnings: list[str],
    ) -> None:
        unlabeled_numeric_columns: set[str] = set()
        for row in sheet.iter_rows():
            non_empty = [cell for cell in row if cell.value is not None]
            if not non_empty:
                continue
            # Keep physical column positions, including empty cells between populated
            # cells. Dropping them can silently attach an amount to the wrong field.
            cells = row[non_empty[0].column - 1 : non_empty[-1].column]
            values = [cell.value for cell in cells]
            formulas = [cell.data_type == TYPE_FORMULA for cell in cells]
            columns = [get_column_letter(cell.column) for cell in cells]
            for cell in non_empty:
                if (
                    cell.row <= 1
                    or isinstance(cell.value, bool)
                    or not isinstance(cell.value, (int, float))
                ):
                    continue
                heading = sheet.cell(row=1, column=cell.column).value
                if heading is None or not str(heading).strip():
                    unlabeled_numeric_columns.add(get_column_letter(cell.column))
            row_metadata: dict[str, Any] = {
                "values": values,
                "formulas": formulas,
                "columns": columns,
            }
            if cached_sheet is not None:
                # ``iter_rows`` cells always carry their integer coordinates; the openpyxl stubs
                # type them as optional, so the invariant is stated here rather than guessed at.
                cached_values: list[Any] = [
                    cached_sheet.cell(cast("int", cell.row), cast("int", cell.column)).value
                    for cell in cells
                ]
                row_metadata["cached_values"] = cached_values
                for cell, cached_value in zip(cells, cached_values, strict=True):
                    if cell.data_type == TYPE_FORMULA and cached_value is None:
                        # Never fabricate a result: a formula with no cached value is unknown.
                        warnings.append(
                            f"Sheet {sheet.title} cell {cell.coordinate}: formula has no cached "
                            "value; its result stayed unrecorded rather than assumed"
                        )
            blocks.append(
                ContentBlock(
                    len(blocks) + 1,
                    "table_row",
                    " | ".join("" if value is None else str(value) for value in values),
                    {
                        "sheet": sheet.title,
                        "range": f"{cells[0].coordinate}:{cells[-1].coordinate}",
                    },
                    row_metadata,
                )
            )
        for column in sorted(unlabeled_numeric_columns):
            warnings.append(
                f"Sheet {sheet.title}: column {column} contains numbers but its first-row "
                "heading is blank; values need explicit business interpretation"
            )


class ImageProbeParser:
    """Verify uploaded image bytes before a vision call is allowed to spend them.

    The header, not the extension, decides the media type: a file whose bytes are not a
    supported raster image fails the run instead of being sent to the vendor.
    """

    name = "image-probe"
    version = "1.1"
    embedded_origin: ImageAssetOrigin | None = None

    def __init__(
        self, image_ocr: ImageOcrService | None = None, *, capture_confidence: bool = False
    ) -> None:
        self.image_ocr = image_ocr
        #: Phase 2 M8: record the OCR engine's confidence on the image block and in the run summary.
        self.capture_confidence = capture_confidence

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        detected = detect_image_media_type(path.read_bytes()[:12])
        if detected is None:
            raise ValueError("unsupported_image_signature")
        declared = media_type.split(";", 1)[0].lower()
        if declared not in {"", "application/octet-stream"} and declared != detected:
            raise ValueError("image_signature_mismatch")
        blocks: list[ContentBlock] = []
        warnings: list[str] = []
        metadata: dict[str, Any] = {}
        if self.image_ocr is None:
            warnings.append(f"{detected} image requires vision extraction")
        else:
            result = self.image_ocr.read(path, media_type=detected)
            if result.text:
                block_metadata: dict[str, Any] = {}
                if self.capture_confidence:
                    block_metadata["extraction"] = "tesseract"
                    if result.confidence is not None:
                        block_metadata["confidence"] = result.confidence
                    metadata["confidence"] = _confidence_summary(
                        [{"page": 1, "source": "tesseract", "confidence": result.confidence}]
                    )
                blocks.append(
                    ContentBlock(
                        1,
                        "image_ocr",
                        result.text,
                        {"image_index": 1, "extraction": "tesseract"},
                        block_metadata,
                    )
                )
            else:
                warnings.append(result.reason or "IMAGE_OCR_NO_TEXT")
        metadata["filename"] = path.name
        metadata["ocr"] = {"engine": "tesseract" if blocks else None}
        return ParsedDocument(
            source_file_id,
            self.name,
            self.version,
            detected,
            metadata,
            blocks,
            warnings,
            requires_vision=True,
            requires_ocr=not bool(blocks),
        )


def detect_image_media_type(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


class ParserRegistry:
    def __init__(
        self,
        *,
        pdf_ocr: PdfOcrService | None = None,
        image_ocr: ImageOcrService | None = None,
        capture_layout: bool = False,
        capture_cached_values: bool = False,
        capture_structure: bool = False,
        capture_confidence: bool = False,
    ) -> None:
        self._parsers: dict[str, DocumentParser] = {
            ".txt": TxtParser(),
            ".pdf": PdfParser(
                ocr_service=pdf_ocr,
                capture_layout=capture_layout,
                capture_confidence=capture_confidence,
            ),
            ".docx": DocxParser(capture_structure=capture_structure),
            ".xlsx": XlsxParser(capture_cached_values=capture_cached_values),
            ".jpg": ImageProbeParser(image_ocr=image_ocr, capture_confidence=capture_confidence),
            ".jpeg": ImageProbeParser(image_ocr=image_ocr, capture_confidence=capture_confidence),
            ".png": ImageProbeParser(image_ocr=image_ocr, capture_confidence=capture_confidence),
        }

    def for_filename(self, filename: str) -> DocumentParser:
        """Route by extension.

        `application/file_validation.validate_upload` already ties the extension to the declared
        content type, and the image parser re-proves the type from the bytes themselves, so a
        second content-type lookup here would only duplicate that check.
        """

        suffix = Path(filename).suffix.lower()
        try:
            return self._parsers[suffix]
        except KeyError as error:
            raise ValueError(f"No parser registered for {suffix}") from error
